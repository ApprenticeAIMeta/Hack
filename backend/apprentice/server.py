"""ApprenticeAI server: phone and dashboard clients over WebSocket.

    PYTHONPATH=. .venv/bin/uvicorn apprentice.server:app --host 0.0.0.0 --port 8000

  WS  /ws/device?procedure=airplane&run=novice_1
                                     phone: sends {"type": "config", "codec": "hevc", "width", "height"}
                                     then the glasses' HEVC stream as binary messages (or plain JPEG
                                     frames with no config, as tools/feed.py does); receives guidance
                                     events ({"type": "play", "url", "text", "kind"}) as JSON text
  WS  /ws/dashboard                  dashboard: receives state snapshots and frames
  GET /                              dashboard page (web/index.html)
  GET /api/procedure/{name}          compiled procedure
  GET /api/mirror/{name}/{t}.jpg     the expert's frame at time t (the mirror side)
  GET /procedures/...                voice clips and reference frames

One device session at a time (a demo has one novice); a new device connection replaces it.
Every device session is recorded to out/sessions/ (see guide/recorder.py).
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from apprentice.guide.session import Session
from apprentice.progress.crop import read_frames
from apprentice.progress.embed import Embedder

ROOT = Path(__file__).resolve().parents[2]
app = FastAPI(title="ApprenticeAI")
app.mount("/procedures", StaticFiles(directory=ROOT / "procedures"), name="procedures")

_embedder: Embedder | None = None
_session: Session | None = None
_dashboards: set[WebSocket] = set()
_device: WebSocket | None = None
_mirror: dict[str, tuple[float, list[bytes]]] = {}


def embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")


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


async def _broadcast(msg: dict) -> None:
    if msg["type"] == "frame":
        text = json.dumps({"type": "frame", "t": msg["t"], "jpeg": base64.b64encode(msg["jpeg"]).decode()})
    else:
        text = json.dumps(msg)
    for ws in list(_dashboards):
        try:
            await ws.send_text(text)
        except Exception:
            _dashboards.discard(ws)
    if msg["type"] == "state" and _device is not None:
        for ev in msg["events"]:
            if "play" in ev:
                try:
                    await _device.send_text(json.dumps({"type": "play", "kind": ev["kind"], **ev["play"]}))
                except Exception:
                    pass


@app.websocket("/ws/device")
async def device(ws: WebSocket, procedure: str = "airplane", run: str | None = None) -> None:
    global _session, _device
    await ws.accept()
    loop = asyncio.get_running_loop()

    def publish(msg: dict) -> None:  # called from the session's worker thread
        asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)

    if _session:
        _session.close()
    emb = await loop.run_in_executor(None, embedder)
    _session = session = Session(procedure, publish, emb, run=run)
    _device = ws
    await _broadcast({"type": "session", "procedure": procedure, "run": run, "status": "started"})
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
            elif msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        session.close()
        if _device is ws:
            _device = None
        await _broadcast({"type": "session", "procedure": procedure, "status": "ended"})


@app.websocket("/ws/dashboard")
async def dashboard(ws: WebSocket) -> None:
    await ws.accept()
    _dashboards.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _dashboards.discard(ws)
