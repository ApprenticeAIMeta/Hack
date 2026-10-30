"""ApprenticeAI server: phone and dashboard clients over WebSocket.

    PYTHONPATH=. .venv/bin/uvicorn apprentice.server:app --host 0.0.0.0 --port 8000

  WS  /ws/device?procedure=airplane&run=novice_1
                                     phone: sends {"type": "config", "codec": "hevc", "width", "height"}
                                     then the glasses' HEVC stream as binary messages (or plain JPEG
                                     frames with no config, as tools/feed.py does); receives guidance
                                     events ({"type": "play", "url", "text", "kind"}) as JSON text.
                                     A config with "photos": true also gets {"type": "snap", "id"} and
                                     answers {"type": "photo", "id", "jpeg": base64} (no jpeg = failed)
  WS  /ws/dashboard                  dashboard: receives state snapshots and frames
  WS  /ws/guide                      guide screen: the same, with frames thinned to GUIDE_FPS
  GET /                              dashboard page (web/index.html)
  GET /guide?procedure=airplane      guide screen for the novice's phone (web/guide.html)
  GET /api/procedure/{name}          compiled procedure
  GET /api/mirror/{name}/{t}.jpg     the expert's frame at time t (the mirror side)
  POST /api/demo?session=<dir>       replay a recorded session to the screens, no glasses or API calls
                                     (default: the longest in demo/, then in out/sessions/)
  GET /procedures/...                voice clips and reference frames

One device session at a time (a demo has one novice); a new device connection replaces it.
Every device session is recorded to out/sessions/ (see guide/recorder.py).
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from apprentice.guide.session import Session
from apprentice.progress.crop import read_frames
from apprentice.progress.embed import Embedder

ROOT = Path(__file__).resolve().parents[2]
GUIDE_FPS = 2
app = FastAPI(title="ApprenticeAI")
app.mount("/procedures", StaticFiles(directory=ROOT / "procedures"), name="procedures")

_embedder: Embedder | None = None
_session: Session | None = None
_dashboards: set[WebSocket] = set()
_guides: set[WebSocket] = set()
_guide_frame_t = -1e9
_device: WebSocket | None = None
_mirror: dict[str, tuple[float, list[bytes]]] = {}
_demo: asyncio.Task | None = None


def embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/guide")
def guide_page() -> FileResponse:
    return FileResponse(ROOT / "web" / "guide.html")


@app.get("/api/procedure/{name}")
def procedure(name: str) -> dict:
    f = ROOT / "procedures" / name / "procedure.json"
    if not f.exists():
        raise HTTPException(404, "no such procedure")
    return json.loads(f.read_text())


@app.get("/api/mirror/{name}/{t}.jpg")
def mirror(name: str, t: float) -> Response:
    """Expert frame nearest to t, from the 2 fps proxy of the procedure's mirror run."""
    if name not in _mirror:
        from apprentice.progress.model import ProgressModel
        run = ProgressModel.load(ROOT / "procedures" / name / "progress.npz").runs[-1]
        frames, fps = read_frames(ROOT / "out" / "progress" / f"{run}_2fps.mp4")
        _mirror[name] = (fps, [cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes() for f in frames])
    fps, frames = _mirror[name]
    i = min(max(int(round(t * fps)), 0), len(frames) - 1)
    return Response(frames[i], media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


async def _send_all(sockets: set[WebSocket], text: str) -> None:
    for ws in list(sockets):
        try:
            await ws.send_text(text)
        except Exception:
            sockets.discard(ws)


async def _broadcast(msg: dict) -> None:
    global _guide_frame_t
    if msg["type"] == "snap":
        if _device is not None:
            await _device.send_text(json.dumps(msg))
        return
    if msg["type"] == "frame":
        text = json.dumps({"type": "frame", "t": msg["t"], "jpeg": base64.b64encode(msg["jpeg"]).decode()})
        if not 0 <= msg["t"] - _guide_frame_t < 1 / GUIDE_FPS:   # a new session restarts t
            _guide_frame_t = msg["t"]
            await _send_all(_guides, text)
    else:
        text = json.dumps(msg)
        await _send_all(_guides, text)
    await _send_all(_dashboards, text)
    if msg["type"] == "state" and _device is not None:
        for ev in msg["events"]:
            if "play" in ev:
                try:
                    await _device.send_text(json.dumps({"type": "play", "kind": ev["kind"], **ev["play"]}))
                except Exception:
                    pass


def _recordings() -> list[Path]:
    """Playable recordings, best last: bundled demo/ runs win over out/sessions/, then the longest."""
    found = [d for base in (ROOT / "demo", ROOT / "out" / "sessions") for d in base.glob("*")
             if (d / "stream.mp4").exists() and (d / "ticks.jsonl").exists()]
    return sorted(found, key=lambda d: (d.parent.name == "demo", (d / "ticks.jsonl").stat().st_size))


def _read_recording(d: Path) -> tuple[list[tuple[float, bytes]], list[dict]]:
    arrivals = [json.loads(l)["t"] for l in (d / "frames.jsonl").read_text().splitlines()]
    ticks = [json.loads(l) for l in (d / "ticks.jsonl").read_text().splitlines()]
    frames, _ = read_frames(d / "stream.mp4")
    # The phone sends parameter sets inside access units, so frames line up with units from the end of the list
    times = arrivals[-len(frames):] if len(frames) <= len(arrivals) else [i / 7 for i in range(len(frames))]
    return [(t, cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()) for t, f in zip(times, frames)], ticks


async def _play(d: Path) -> None:
    meta = json.loads((d / "meta.json").read_text())
    frames, ticks = await asyncio.get_running_loop().run_in_executor(None, _read_recording, d)
    timeline = sorted([(t, 0, {"type": "frame", "t": t, "jpeg": j}) for t, j in frames]
                      + [(m["t"], 1, m) for m in ticks], key=lambda x: x[:2])
    await _broadcast({"type": "session", "procedure": meta["procedure"], "run": meta["run"], "status": "started", "mode": "replay"})
    t0 = asyncio.get_running_loop().time()
    for t, _, msg in timeline:
        await asyncio.sleep(max(0.0, t0 + t - asyncio.get_running_loop().time()))
        await _broadcast(msg)
    await _broadcast({"type": "session", "procedure": meta["procedure"], "status": "ended", "mode": "replay"})


def _stop_demo() -> None:
    global _demo
    if _demo and not _demo.done():
        _demo.cancel()
    _demo = None


@app.post("/api/demo")
async def demo(session: str | None = None) -> dict:
    global _demo
    if _device is not None:
        raise HTTPException(409, "the glasses are streaming; stop them to play a recording")
    runs = _recordings()
    if session is not None:
        if not re.fullmatch(r"[\w-]+", session):
            raise HTTPException(400, "bad session name")
        runs = [d for d in runs if d.name == session]
    if not runs:
        raise HTTPException(404, "no recording; run a session first")
    _stop_demo()
    _demo = asyncio.create_task(_play(runs[-1]))
    return {"session": runs[-1].name}


@app.websocket("/ws/device")
async def device(ws: WebSocket, procedure: str = "airplane", run: str | None = None) -> None:
    global _session, _device
    await ws.accept()
    _stop_demo()
    loop = asyncio.get_running_loop()

    def publish(msg: dict) -> None:  # called from the session's worker thread
        asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)

    if _session:
        _session.close()
    emb = await loop.run_in_executor(None, embedder)
    _session = session = Session(procedure, publish, emb, run=run)
    _device = ws
    await _broadcast({"type": "session", "procedure": procedure, "run": run, "status": "started", "mode": "live"})
    hevc = False
    try:
        while True:
            msg = await ws.receive()
            if msg.get("bytes"):
                (session.push_hevc if hevc else session.push_jpeg)(msg["bytes"])
            elif msg.get("text"):
                cfg = json.loads(msg["text"])
                if cfg.get("type") == "config" and cfg.get("codec") == "hevc":
                    hevc = True
                    session.configure_hevc(int(cfg["width"]), int(cfg["height"]), cfg.get("fps"))
                    session.photos_on = bool(cfg.get("photos"))
                elif cfg.get("type") == "photo":
                    session.on_photo(int(cfg["id"]), base64.b64decode(cfg["jpeg"]) if cfg.get("jpeg") else None)
            elif msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
        if _device is ws:
            _device = None
        await _broadcast({"type": "session", "procedure": procedure, "status": "ended", "mode": "live"})


async def _viewer(ws: WebSocket, sockets: set[WebSocket]) -> None:
    await ws.accept()
    sockets.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        sockets.discard(ws)


@app.websocket("/ws/dashboard")
async def dashboard(ws: WebSocket) -> None:
    await _viewer(ws, _dashboards)


@app.websocket("/ws/guide")
async def guide(ws: WebSocket) -> None:
    await _viewer(ws, _guides)
