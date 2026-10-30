"""A live guide session: glasses frames in, guidance events and dashboard state out.

Frames arrive at the stream rate (7 fps). The frame gate keeps the sharp, steady ones; the
worker takes the newest kept frame whenever it is free (so it never falls behind) and runs
one tick:
  SAM 3.1 (image) finds the workpiece and hands -> crop -> DINOv2 -> progress + similarity
  -> step tracker, whose Spark checks run in a thread pool and land on later ticks.
Every tick publishes a state snapshot; guidance events carry the expert clip to play.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import av
import cv2
import numpy as np
from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt

from apprentice.capture.frame_gate import LiveGate
from apprentice.guide.policy import Check, SparkPolicy
from apprentice.guide.recorder import Recorder
from apprentice.guide.state import Tracker, step_ranges
from apprentice.models.meta import SAM, MetaClient
from apprentice.progress.crop import _touches, crop, parse_lines
from apprentice.progress.embed import Embedder
from apprentice.progress.model import ProgressModel

ROOT = Path(__file__).resolve().parents[3]
SMOOTH = 5
REACQUIRE = 10
SPEND_CAP_USD = float(os.environ.get("SPEND_CAP_USD", "10"))   # across all sessions of this server


def _iou(a, b) -> float:
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def _plausible(prev, box, min_iou: float = 0.2, max_scale: float = 3.0) -> bool:
    """A box-prompt result that jumped away or changed size wildly has latched onto something else
    (seen live: the sheet's box collapsed onto a corner of the laptop and progress spiked to 96%)."""
    ap = (prev[2] - prev[0]) * (prev[3] - prev[1])
    ab = (box[2] - box[0]) * (box[3] - box[1])
    return _iou(prev, box) >= min_iou and 1 / max_scale <= ab / max(ap, 1) <= max_scale


class _FutureHandle:
    def __init__(self, fut: Future):
        self.fut = fut

    def poll(self, now: float):
        if not self.fut.done():
            return None
        try:
            return now, self.fut.result()
        except Exception as e:  # a failed check counts as unclear, never as an answer
            return now, Check(False, 0.0, f"check failed: {e}"[:200], 0.0, 0.0, unclear=True)


class Session:
    spent_usd = 0.0     # closed sessions, so a restarted stream cannot walk past the cap

    def __init__(self, procedure: str, publish: Callable[[dict], None], embedder: Embedder,
                 client: MetaClient | None = None, run: str | None = None, record: bool = True):
        self.name = procedure
        self.dir = ROOT / "procedures" / procedure
        self.proc = json.loads((self.dir / "procedure.json").read_text())
        self.model = ProgressModel.load(self.dir / "progress.npz")
        self.expert = self.model.runs[-1]
        self.client = client or MetaClient()
        self.policy = SparkPolicy(self.dir, self.client)
        self.embedder = embedder
        self.pool = ThreadPoolExecutor(4)
        self.tracker = Tracker(step_ranges(self.proc), self._ask)
        self.gate = LiveGate(max_fps=2.0)
        self.recorder = Recorder(procedure, run, len(self.proc["steps"])) if record else None
        self._publish = publish
        self.t0 = time.time()
        self.box: tuple | None = None
        self.ticks = 0
        self.raw: list[float] = []
        self.curve: list[tuple[float, float]] = []
        self.voice = {v["turn_id"]: v for s in self.proc["steps"] for v in s["voice"]}
        self.cost = 0.0
        self.capped = False
        self._latest: tuple[float, np.ndarray] | None = None
        self._decoder: av.CodecContext | None = None
        self.frames_in = self.frames_gated = 0
        self._size: tuple[int, int] | None = None
        self._cv = threading.Condition()
        self._stop = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def publish(self, msg: dict) -> None:
        if self.recorder and msg["type"] == "state":
            self.recorder.state(msg)
        self._publish(msg)

    # -- input ---------------------------------------------------------------------------

    def push_jpeg(self, data: bytes) -> None:
        """A frame as JPEG (tools/feed.py replays recordings this way)."""
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            self._on_frame(frame, data)

    def configure_hevc(self, width: int, height: int, fps: int | None = None) -> None:
        """The phone forwards the glasses' compressed HEVC stream as is (Annex B, ~55 kbps), one
        access unit per message; it is decoded in-process. (An ffmpeg pipe lost the first ~3 s:
        its stream probe swallowed the keyframe.)"""
        if self._decoder is None or self._size != (width, height):
            self._decoder = av.CodecContext.create("hevc", "r")
            self._size = (width, height)
            if self.recorder:
                self.recorder.config(width, height, fps)

    def push_hevc(self, data: bytes) -> None:
        if self._decoder is None:
            return
        if self.recorder:
            self.recorder.unit(time.time() - self.t0, data)
        try:
            for pkt in self._decoder.parse(data):
                for fr in self._decoder.decode(pkt):
                    self._on_frame(fr.to_ndarray(format="bgr24"))
        except av.error.InvalidDataError:
            pass  # a corrupt unit from a Bluetooth hiccup; the next keyframe recovers

    def _on_frame(self, frame: np.ndarray, jpeg: bytes | None = None) -> None:
        t = time.time() - self.t0
        if jpeg is None:
            jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
        self.publish({"type": "frame", "t": round(t, 2), "jpeg": jpeg})
        self.frames_in += 1
        picked = self.gate.offer(frame, t)
        if picked is not None:
            self.frames_gated += 1
            with self._cv:
                self._latest = (t, picked)
                self._cv.notify()

    def close(self) -> None:
        self._decoder = None
        self._stop = True
        Session.spent_usd += self.cost
        if self.recorder:
            self.recorder.close()
        with self._cv:
            self._cv.notify()
        self.pool.shutdown(wait=False, cancel_futures=True)

    # -- checks --------------------------------------------------------------------------

    def _ask(self, kind: str, step: int, frame: np.ndarray, t: float):
        fn = (lambda: self.policy.locate(frame)) if kind == "done" else (lambda: self.policy.mistake(step, frame))
        return _FutureHandle(self.pool.submit(fn))

    def _sam(self, frame: np.ndarray, prompt: str) -> list[tuple]:
        """(x1, y1, x2, y2, conf) per match with conf >= 0.5. `prompt` is a noun phrase or box tokens."""
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        url = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
        r = self.client.respond_stream([{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": url}]}],
            model=SAM, metadata={"include_confidence": "true", "mask_encoding": "one_bit"})
        self.cost += 2.5 / 1000
        return [r_[1:] for r_ in parse_lines(r.text).get(0, []) if r_[5] >= 0.5]

    def _workpiece(self, frame: np.ndarray) -> tuple | None:
        """Follow the last box with a SAM box prompt (stable on blurry frames); when that loses it,
        find the workpiece again by name, keeping the match the hands touch. Re-finding by name every
        REACQUIRE ticks keeps a box-prompt track from drifting onto a hand for good."""
        self.ticks += 1
        if self.box is not None and self.ticks % REACQUIRE:
            H, W = frame.shape[:2]
            x1, y1, x2, y2 = self.box
            prompt = build_object_prompt(size=(W, H), objects=[PromptObject(
                id=1, prompts=[FramePrompt(frame=0, box=PromptBox(x1, y1, min(x2 + 1, W), min(y2 + 1, H)))])])
            got = self._sam(frame, prompt)
            if got and _plausible(self.box, got[0][:4]):
                return tuple(got[0][:4])
        with ThreadPoolExecutor(2) as ex:
            wp, hands = ex.map(lambda p: self._sam(frame, p), [self.proc["workpiece"], "hand"])
        touched = [r for r in wp if any(_touches(r[:4], h[:4]) for h in hands)]
        if not touched:      # nothing in the hands: no tick beats a tick on the wrong object
            return None

        def score(r):
            area = (r[2] - r[0]) * (r[3] - r[1])
            if self.box is None:
                return area * r[4]
            bx = self.box
            inter = max(0, min(r[2], bx[2]) - max(r[0], bx[0])) * max(0, min(r[3], bx[3]) - max(r[1], bx[1]))
            union = area + (bx[2] - bx[0]) * (bx[3] - bx[1]) - inter
            return inter / union + 0.1 * r[4]
        return tuple(max(touched, key=score)[:4])

    # -- tick ----------------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop:
            with self._cv:
                while self._latest is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                t, frame = self._latest
                self._latest = None
            try:
                self._tick(t, frame)
            except Exception as e:
                self.publish({"type": "error", "t": round(t, 2), "message": str(e)[:300]})

    def _tick(self, t: float, frame: np.ndarray) -> None:
        if Session.spent_usd + self.cost >= SPEND_CAP_USD:     # stops new calls; frames keep recording
            if not self.capped:
                self.capped = True
                self.publish({"type": "capped", "t": round(t, 2), "cap_usd": SPEND_CAP_USD})
            return
        t_start = time.time()
        box = self._workpiece(frame)
        t_sam = time.time()
        p = sim = None
        view = None
        if box:
            self.box = box
            pred = self.model.predict(self.embedder([crop(frame, box)]))
            self.raw.append(float(pred["progress"][0]))
            p = float(np.median(self.raw[-SMOOTH:]))
            sim = float(pred["similarity"][0])
            self.curve.append((round(t, 2), round(p, 4)))
            view = self._view(frame, box)
        t_model = time.time()
        if box:
            self.tracker.tick(t, p, sim, view)
        events = []
        for e in self.tracker.drain():
            ev = {"t": e.t, "kind": e.kind, "step": e.step, **e.data}
            if e.kind == "check":
                self.cost += e.data.get("cost") or 0.0
            if e.kind == "instruct" and self.proc["steps"][e.step]["voice"]:
                ev["play"] = self._clip(self.proc["steps"][e.step]["voice"][0])
            if e.kind == "warn" and e.data.get("utterance_id") in self.voice:
                ev["play"] = self._clip(self.voice[e.data["utterance_id"]])
            events.append(ev)
        mirror_t = self.model.mirror(p, self.expert) if p is not None else None
        self.publish({"type": "state", "t": round(t, 2), "progress": p, "similarity": sim, "box": box,
                      "frame_size": [frame.shape[1], frame.shape[0]], "step": self.tracker.k,
                      "finished": self.tracker.finished, "mirror_t": mirror_t, "events": events,
                      "tick_ms": round(1000 * (time.time() - t_start)), "cost_usd": round(self.cost, 4),
                      "frames_in": self.frames_in, "frames_gated": self.frames_gated,
                      "timing_ms": {"sam": round(1000 * (t_sam - t_start)), "model": round(1000 * (t_model - t_sam)),
                                    "tracker": round(1000 * (time.time() - t_model))}})

    def _view(self, frame: np.ndarray, box: tuple, pad: float = 0.3, out_w: int = 640) -> np.ndarray:
        H, W = frame.shape[:2]
        x1, y1, x2, y2 = box
        side = max(x2 - x1, y2 - y1) * (1 + 2 * pad)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        a, b = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        c, d = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
        v = frame[b:d, a:c]
        return cv2.resize(v, (out_w, round(v.shape[0] * out_w / v.shape[1])), interpolation=cv2.INTER_CUBIC)

    def _clip(self, v: dict) -> dict:
        return {"url": f"/procedures/{self.name}/{v['file']}", "text": v["text"]}
