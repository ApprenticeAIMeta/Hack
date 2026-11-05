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
  GET /build?procedure=thermocouple  what one recording became: his take, timed words, steps, his voice
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
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from apprentice.guide.session import Session
from apprentice.models.omni_worker import OmniASR
from apprentice.progress.crop import read_frames
from apprentice.progress.embed import Embedder
from apprentice.progress.fuse import TaskProgress
from apprentice.progress.model import ProgressModel

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
_last_session: dict | None = None
_frame_busy = False          # a frame fan-out in flight: newer frames replace it rather than queue behind it
_device_lock = asyncio.Lock()


def embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


BUILD = ROOT / "web" / "build"   # React pages built from web/app; the plain pages in web/ are the fallback
if (BUILD / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=BUILD / "assets"), name="assets")


def _page(name: str) -> FileResponse:
    built = BUILD / name
    return FileResponse(built if built.exists() else ROOT / "web" / name)


@app.get("/")
def index() -> FileResponse:
    return _page("index.html")


@app.get("/guide")
def guide_page() -> FileResponse:
    return _page("guide.html")


@app.get("/build")
def build_page() -> FileResponse:
    """What one expert recording became: his take, his timed words, the steps, and his voice."""
    return _page("build.html")


def _proc_dir(name: str) -> Path:
    if not re.fullmatch(r"[\w-]+", name) or not (ROOT / "procedures" / name / "procedure.json").exists():
        raise HTTPException(404, "no such procedure")
    return ROOT / "procedures" / name


@app.get("/api/source/{name}")
def source_video(name: str) -> FileResponse:
    """The expert's recording the procedure was compiled from (footage/, kept out of git)."""
    src = json.loads((_proc_dir(name) / "procedure.json").read_text()).get("source", {}).get("video", "")
    f = ROOT / "footage" / Path(src).name
    if not src or not f.exists():
        raise HTTPException(404, "recording not on this machine")
    return FileResponse(f, media_type="video/mp4")


@app.get("/api/voice_sample/{name}")
def voice_sample(name: str) -> FileResponse:
    """The stretch of the expert's own recording his voice was cloned from (tools/new_procedure.sh)."""
    _proc_dir(name)
    f = ROOT / "out" / "voice" / name / "reference.wav"
    if not f.exists():
        raise HTTPException(404, "no voice sample on this machine")
    return FileResponse(f, media_type="audio/wav")


@app.get("/api/procedure/{name}")
def procedure(name: str, lang: str = "en") -> dict:
    """The compiled procedure; with ?lang=xx, titles, instructions, whys and known mistakes are
    replaced by the translation from compile/localize.py when one exists."""
    f = ROOT / "procedures" / name / "procedure.json"
    if not f.exists():
        raise HTTPException(404, "no such procedure")
    proc = json.loads(f.read_text())
    lf = ROOT / "procedures" / name / "lang" / f"{lang}.json"
    if lang != "en" and re.fullmatch(r"[a-z]{2,3}", lang) and lf.exists():
        local = json.loads(lf.read_text())
        by_id = {st["id"]: st for st in local["steps"]}
        for st in proc["steps"]:
            st.update({k: by_id[st["id"]][k] for k in ("title", "instruction", "why", "known_mistakes") if st["id"] in by_id})
        proc["task"] = local["task"]
        proc["lang"] = lang
        proc["language"] = local.get("language", lang)
    return proc


@app.get("/api/mirror/{name}/{t}.jpg")
def mirror(name: str, t: float) -> Response:
    """Expert frame nearest to t, from the 2 fps proxy of the procedure's mirror run."""
    if name not in _mirror:
        from apprentice.progress.model import ProgressModel
        run = ProgressModel.load(ROOT / "procedures" / name / "progress.npz").runs[-1]
        # The training cache is local; a procedure bundled for replay carries its own copy
        src = ROOT / "out" / "progress" / f"{run}_2fps.mp4"
        if not src.exists():
            src = ROOT / "procedures" / name / "mirror_2fps.mp4"
        frames, fps = read_frames(src)
        _mirror[name] = (fps, [cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes() for f in frames])
    fps, frames = _mirror[name]
    i = min(max(int(round(t * fps)), 0), len(frames) - 1)
    return Response(frames[i], media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


SEND_TIMEOUT_S = 1.0   # a viewer that stops reading (closed laptop, killed tab) must not stall the rest


async def _send_all(sockets: set[WebSocket], text: str) -> None:
    for ws in list(sockets):
        try:
            await asyncio.wait_for(ws.send_text(text), SEND_TIMEOUT_S)
        except Exception:
            sockets.discard(ws)


async def _broadcast(msg: dict) -> None:
    global _guide_frame_t, _last_session
    global _frame_busy
    if msg["type"] == "snap":
        if _device is not None:
            async with _device_lock:
                await _device.send_text(json.dumps(msg))
        return
    if msg["type"] == "frame":
        if _frame_busy:
            return                      # a slow viewer must not build a backlog of frames
        _frame_busy = True
        try:
            text = json.dumps({"type": "frame", "t": msg["t"], "jpeg": base64.b64encode(msg["jpeg"]).decode()})
            if not 0 <= msg["t"] - _guide_frame_t < 1 / GUIDE_FPS:   # a new session restarts t
                _guide_frame_t = msg["t"]
                await _send_all(_guides, text)
            await _send_all(_dashboards, text)
        finally:
            _frame_busy = False
        return
    else:
        if msg["type"] == "session":
            _last_session = msg
        text = json.dumps(msg)
        await _send_all(_guides, text)
    await _send_all(_dashboards, text)
    if msg["type"] == "state" and _device is not None:
        for ev in msg["events"]:
            if ev.get("play") and ev["play"].get("url"):
                try:
                    async with _device_lock:
                        await _device.send_text(json.dumps({"type": "play", "kind": ev["kind"], **ev["play"]}))
                except Exception:
                    pass


def _recordings() -> list[Path]:
    """Playable recordings, best last: bundled demo/ runs win over out/sessions/, then the longest."""
    found = [d for base in (ROOT / "demo", ROOT / "out" / "sessions") for d in base.glob("*")
             if (d / "stream.mp4").exists() and (d / "ticks.jsonl").exists()]
    return sorted(found, key=lambda d: (d.parent.name == "demo", (d / "ticks.jsonl").stat().st_size))


REPLAY_FPS, REPLAY_H = int(os.environ.get("REPLAY_FPS", 8)), int(os.environ.get("REPLAY_H", 896))  # replays are for review: decode as they play, thinned and scaled


def _regauge(procedure: str, ticks: list[dict]) -> list[dict]:
    """Runs recorded before the step-anchored gauge (progress/fuse.py) carry the raw DINOv2 value as
    "progress"; recompute the gauge and the mirror time from it so every replay shows the same gauge."""
    states = [m for m in ticks if m.get("type") == "state"]
    if not states or any("progress_raw" in m for m in states):
        return ticks
    pdir = ROOT / "procedures" / procedure
    model = ProgressModel.load(pdir / "progress.npz")
    gauge = TaskProgress(json.loads((pdir / "procedure.json").read_text()), model, model.runs[0])
    shown = None
    for m in states:
        raw = m.get("progress")
        if "frame_size" in m and m.get("step") is not None:
            shown = gauge.update(m["t"], m["step"], bool(m.get("finished")), raw)
        m["progress_raw"], m["progress"] = raw, shown
        if shown is not None:
            m["mirror_t"] = model.mirror(gauge.label_at(shown), model.runs[-1])
    return ticks


_frame_tasks: set[asyncio.Task] = set()


async def _play(d: Path, speed: float = 1.0) -> None:
    """Replay a saved run in real time (or faster), decoding the video as it goes rather than up front."""
    loop = asyncio.get_running_loop()
    meta = json.loads((d / "meta.json").read_text())
    arrivals = [json.loads(l)["t"] for l in (d / "frames.jsonl").read_text().splitlines()]
    ticks = _regauge(meta["procedure"], [json.loads(l) for l in (d / "ticks.jsonl").read_text().splitlines()])
    cap = cv2.VideoCapture(str(d / "stream.mp4"))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or len(arrivals)
    fps = cap.get(cv2.CAP_PROP_FPS) or 7
    # The phone sends parameter sets inside access units, so frames line up with units from the end of the list
    times = arrivals[-n:] if 0 < n <= len(arrivals) else [i / fps for i in range(n)]

    def next_jpeg(i: int) -> bytes | None:
        ok, f = cap.read()
        if not ok:
            return None
        if i % max(1, round(fps / REPLAY_FPS)):
            return b""
        if f.shape[0] > REPLAY_H:
            f = cv2.resize(f, (round(f.shape[1] * REPLAY_H / f.shape[0]), REPLAY_H), interpolation=cv2.INTER_AREA)
        return cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()

    await _broadcast({"type": "session", "procedure": meta["procedure"], "run": meta["run"], "status": "started",
                      "mode": "replay", "session": d.name, "speed": speed})
    t0, k = loop.time(), 0

    async def until(t: float) -> None:
        await asyncio.sleep(max(0.0, t0 + t / speed - loop.time()))

    try:
        for i, t in enumerate(times):
            jpeg = await loop.run_in_executor(None, next_jpeg, i)
            if jpeg is None:
                break
            while k < len(ticks) and ticks[k]["t"] <= t:
                await until(ticks[k]["t"])
                await _broadcast(ticks[k])
                k += 1
            if jpeg and loop.time() - (t0 + t / speed) > 0.25:
                continue                # behind the run's clock (a slow viewer): drop frames to catch up
            if jpeg:
                await until(t)
                # not awaited: a slow viewer drops frames (_frame_busy) instead of slowing the replay's clock
                task = asyncio.create_task(_broadcast({"type": "frame", "t": t, "jpeg": jpeg}))
                _frame_tasks.add(task); task.add_done_callback(_frame_tasks.discard)
        for m in ticks[k:]:
            await until(m["t"])
            await _broadcast(m)
    finally:
        cap.release()
    await _broadcast({"type": "session", "procedure": meta["procedure"], "status": "ended", "mode": "replay"})


def _stop_demo() -> None:
    global _demo
    if _demo and not _demo.done():
        _demo.cancel()
    _demo = None


@app.get("/api/sessions")
def sessions() -> list[dict]:
    """Saved runs, newest first, for replaying on the dashboard."""
    out = []
    for d in reversed(_recordings()):
        try:
            m = json.loads((d / "meta.json").read_text())
        except (OSError, ValueError):
            continue
        out.append({"session": d.name, "run": m.get("run"), "procedure": m.get("procedure"), "started": m.get("started"),
                    "duration_s": m.get("duration_s"), "steps_done": len(m.get("steps_done", [])),
                    "warnings": len(m.get("warnings", [])), "complete": m.get("complete"),
                    "finished_s": m["steps_done"][-1][0] if m.get("complete") and m.get("steps_done") else None})
    return sorted(out, key=lambda r: r["started"] or "", reverse=True)


@app.get("/api/recorded/{session}/asks/{name}")
def recorded_ask(session: str, name: str) -> FileResponse:
    """A recorded answer's audio, for replays (Recorder.ask)."""
    if not re.fullmatch(r"[\w-]+", session) or not re.fullmatch(r"\d+\.wav", name):
        raise HTTPException(400, "bad name")
    for base in (ROOT / "demo", ROOT / "out" / "sessions"):
        f = base / session / "asks" / name
        if f.exists():
            return FileResponse(f, media_type="audio/wav")
    raise HTTPException(404, "no such recording")


@app.get("/api/spoken/{i}.wav")
def spoken(i: int) -> Response:
    if _session is None or i not in _session.spoken:
        raise HTTPException(404, "no such line")
    return Response(_session.spoken[i], media_type="audio/wav")


_omni = OmniASR()      # loads in the background when the glasses first connect


def _hear(wav: Path, lang: str) -> tuple[str, str, float]:
    """The novice's question as text: Muse Voice Transcribe first in English, Meta's Omnilingual
    ASR (local, open weights) first in other languages (closer on a Hindi test question, but about
    7 s warm against about 1 s), each the other's fallback when it fails or hears nothing.
    ASK_ASR_FIRST=voice-transcribe or omnilingual overrides the order. Returns (text, model, seconds)."""
    def vt():
        return _session.client.transcribe(wav, mode="PUSH_TO_TALK").text.strip()
    def omni():
        return (_omni.transcribe(wav) or "").strip()
    order = [("muse-voice-transcribe", vt), ("omnilingual-asr", omni)]
    first = os.environ.get("ASK_ASR_FIRST", "auto")      # auto | voice-transcribe | omnilingual
    if _omni.available() and (first == "omnilingual" or (first == "auto" and lang != "en")):
        order.reverse()
    t0 = time.perf_counter()
    for name, fn in order:
        try:
            text = fn()
        except Exception as e:
            print(f"ask: {name} failed: {e}", flush=True)
            continue
        if text:
            return text, name, time.perf_counter() - t0
    return "", order[0][0], time.perf_counter() - t0


@app.post("/api/ask")
async def ask(request: Request, lang: str = "en", q: str | None = None) -> dict:
    """The novice's "hold to ask" button: the body is the recorded question (m4a/wav/webm bytes).
    Muse Voice Transcribe hears it, Muse Spark answers as the expert, MMS speaks it; the answer is
    played on the glasses and shown on the phone and dashboard."""
    if _session is None:
        raise HTTPException(409, "no live session")
    audio = await request.body()
    if q is None and len(audio) < 2000:
        raise HTTPException(400, "no audio")
    loop = asyncio.get_running_loop()

    def work() -> dict:
        if q is not None:                 # a typed question (testing, or a scripted demo beat): no transcription
            out = _session.ask(q.strip())
            out["asr_s"] = None
            return out
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "q.bin"
            src.write_bytes(audio)
            wav = Path(d) / "q.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(wav)], check=True)
            heard, asr_model, t_asr = _hear(wav, _session.lang)
        print(f"ask: {asr_model} heard {heard!r} in {t_asr:.1f} s", flush=True)
        if not heard:
            return {"question": "", "answer": "", "url": None, "asr_s": round(t_asr, 2)}
        out = _session.ask(heard)
        out["asr_model"] = asr_model
        print(f"ask: answered {out['answer']!r} in {out['latency_s']} s", flush=True)
        out["asr_s"] = round(t_asr, 2)
        return out

    out = await loop.run_in_executor(None, work)
    if out["answer"]:
        msg = {"type": "ask", "t": round(time.time() - _session.t0, 2), "step": out["step"],
               "question": out["question"], "answer": out["answer"], "url": out["url"],
               "asr_s": out.get("asr_s"), "latency_s": out.get("latency_s"), "clip": out.get("clip"),
               "model": out.get("model"), "asr_model": out.get("asr_model")}
        await _broadcast(msg)
        if _session.recorder:
            i = int(out["url"].rsplit("/", 1)[1].split(".")[0]) if out["url"] else None
            _session.recorder.ask(msg, _session.spoken.get(i))
        if _device is not None:
            async with _device_lock:
                await _device.send_text(json.dumps({"type": "play", "kind": "ask", "url": out["url"], "text": out["answer"]}))
    return out


@app.post("/api/goto")
def goto(step: int) -> dict:
    """The novice picked a step on the phone (step is 0-based)."""
    if _session is None or _device is None:
        raise HTTPException(409, "no live session")
    _session.request_goto(step)
    return {"ok": True, "step": step}


@app.post("/api/check")
def check() -> dict:
    """The novice's "check my step" button."""
    if _session is None or _device is None:
        raise HTTPException(409, "no live session")
    _session.request_check()
    return {"ok": True}


@app.post("/api/demo")
async def demo(session: str | None = None, speed: float = 1.0) -> dict:
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
    _demo = asyncio.create_task(_play(runs[-1], min(max(speed, 0.25), 8.0)))
    return {"session": runs[-1].name}


@app.websocket("/ws/device")
async def device(ws: WebSocket, procedure: str = "airplane", run: str | None = None, lang: str = "en") -> None:
    global _session, _device
    _omni.start()
    await ws.accept()
    _stop_demo()
    loop = asyncio.get_running_loop()

    def publish(msg: dict) -> None:  # called from the session's worker thread
        asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)

    if _session:
        try:
            _session.close()
        except Exception as e:   # a broken old session must never refuse the new connection
            print(f"device: closing the previous session failed: {e}", flush=True)
    emb = await loop.run_in_executor(None, embedder)
    _session = session = Session(procedure, publish, emb, run=run, lang=lang if re.fullmatch(r"[a-z]{2,3}", lang or "") else "en")
    _device = ws
    await _broadcast({"type": "session", "procedure": procedure, "run": run, "status": "started", "mode": "live", "lang": session.lang})
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
    if _last_session is not None:   # a page opened mid-session still learns whether it is live or a replay
        await ws.send_text(json.dumps(_last_session))
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
