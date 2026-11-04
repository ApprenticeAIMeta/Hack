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
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import av
import cv2
import httpx
import numpy as np
from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt

from apprentice.capture.frame_gate import LiveGate
from apprentice.guide.policy import Check, SparkPolicy
from apprentice.guide.recorder import Recorder
from apprentice.guide.state import Tracker, step_ranges
from apprentice.models.meta import SAM, MetaClient
from apprentice.models.speech import speaker
from apprentice.progress.crop import _touches, crop, parse_lines
from apprentice.progress.embed import Embedder
from apprentice.progress.model import ProgressModel

ROOT = Path(__file__).resolve().parents[3]
SMOOTH = 5
REACQUIRE = 10
PHOTO_MIN_GAP_S = 6.0   # each still freezes the stream for 2-3 s, so most ticks must not ask for one
PHOTO_TIMEOUT_S = 8.0   # the phone holds a still until the voice line has finished
STILL_FRESH_S = 10.0    # a check asked soon after a still may judge the same still
SAM_TIMEOUT = httpx.Timeout(6.0, connect=3.0)   # one slow SAM call must not stall the guide (17 s seen live)
DASH_FPS, DASH_H = 8, 640   # dashboard frames: thinned and downscaled before the base64 fan-out
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
                 client: MetaClient | None = None, run: str | None = None, record: bool = True, lang: str = "en"):
        self.name = procedure
        self.dir = ROOT / "procedures" / procedure
        self.proc = json.loads((self.dir / "procedure.json").read_text())
        # The novice's language: translated lines and MMS voice from compile/localize.py, when present
        self.lang = lang if (self.dir / "lang" / f"{lang}.json").exists() else "en"
        self.local = json.loads((self.dir / "lang" / f"{lang}.json").read_text()) if self.lang != "en" else None
        self._local_steps = {st["id"]: st for st in self.local["steps"]} if self.local else {}
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
        self.photos_on = False
        self._photo_lock = threading.Lock()   # the toolkit allows one capture in flight
        self._photo_id = 0
        self._photo_got: tuple[threading.Event, list] | None = None
        self._photo_last = -1e9
        self.photos_used = self.photos_failed = 0
        self._still_view: tuple[float, np.ndarray] | None = None   # (taken at, cropped still)
        self._deliver_due = False      # a check finished; hand it out even if no frame arrives
        self._dash_t = -1e9
        self.sam_failures = 0
        self.spoken: dict[int, bytes] = {}    # pre-rendered warning lines, served at /api/spoken/<id>.wav
        self._spoken_ids: dict[str, int] = {}
        self._cv = threading.Condition()
        self._stop = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        threading.Thread(target=self._prerender, daemon=True).start()   # after _stop exists

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
        self.frames_in += 1
        # Viewers get at most DASH_FPS frames, downscaled: encoding and fanning out every 720p frame
        # as base64 JSON was most of the event loop's work during a live run
        if t - self._dash_t >= 1 / DASH_FPS:
            self._dash_t = t
            if jpeg is None:
                small = frame if frame.shape[0] <= DASH_H else cv2.resize(
                    frame, (round(frame.shape[1] * DASH_H / frame.shape[0]), DASH_H), interpolation=cv2.INTER_AREA)
                jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()
            self.publish({"type": "frame", "t": round(t, 2), "jpeg": jpeg})
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
        def run() -> Check:
            if self._stop:
                raise RuntimeError("session closed")
            # "Which step" takes a fresh still (each one freezes the stream 2-3 s); a mistake check
            # judges the still just taken when there is one, else the stream frame
            if kind == "done":
                still = self._still()
            else:
                cached = self._still_view
                still = cached[1] if cached and time.time() - cached[0] < STILL_FRESH_S else None
            if self._stop:
                raise RuntimeError("session closed")
            view = frame if still is None else still
            res = self.policy.locate(view) if kind == "done" else self.policy.mistake(step, view)
            res.source = "stream" if still is None else "photo"
            return res
        fut = self.pool.submit(run)

        def wake(_):
            with self._cv:
                self._deliver_due = True
                self._cv.notify()
        fut.add_done_callback(wake)
        return _FutureHandle(fut)

    # -- stills: the stream tracks the work, Spark judges it on a sharper picture when it can -----

    def on_photo(self, photo_id: int, jpeg: bytes | None) -> None:
        got = self._photo_got
        if got and photo_id == self._photo_id:
            got[1].append(jpeg)
            got[0].set()

    def _still(self) -> np.ndarray | None:
        """A fresh still from the glasses cropped to the workpiece, or None to use the stream frame:
        photos are off, one was taken moments ago or is in flight, or the capture failed."""
        if not self.photos_on or time.time() - self._photo_last < PHOTO_MIN_GAP_S:
            return None
        if not self._photo_lock.acquire(blocking=False):
            return None
        try:
            self._photo_last = time.time()
            self._photo_id += 1
            self._photo_got = (threading.Event(), [])
            self._publish({"type": "snap", "id": self._photo_id})
            arrived = self._photo_got[0].wait(PHOTO_TIMEOUT_S)
            jpeg = self._photo_got[1][0] if arrived else None
            self._photo_got = None
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) if jpeg else None
            if img is None:
                self.photos_failed += 1
                return None
            self.photos_used += 1
            # The glasses deliver stills landscape with the top of the view on the left; the stream is portrait
            if img.shape[1] > img.shape[0]:
                img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
                jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
            if self.recorder:
                self.recorder.photo(time.time() - self.t0, jpeg)
            view = self._view(img, self._still_box(img))
            self._still_view = (time.time(), view)
            return view
        finally:
            self._photo_lock.release()

    def _still_box(self, img: np.ndarray) -> tuple:
        """Where the workpiece is in a still. The still is the same camera as the stream (rotated
        upright above), so the tracked box scaled to the still's size is a good prompt and costs one
        SAM call; re-finding by name costs two and is the fallback when that prompt misses."""
        H, W = img.shape[:2]
        if self.box is not None and self._size:
            sx, sy = W / self._size[0], H / self._size[1]
            x1, y1, x2, y2 = self.box
            guess = (x1 * sx, y1 * sy, x2 * sx, y2 * sy)
            prompt = build_object_prompt(size=(W, H), objects=[PromptObject(
                id=1, prompts=[FramePrompt(frame=0, box=PromptBox(*(int(v) for v in guess)))])])
            got = self._sam(img, prompt)
            if got and _plausible(guess, got[0][:4]):
                return tuple(got[0][:4])
        with ThreadPoolExecutor(2) as ex:
            wp, hands = ex.map(lambda p: self._sam(img, p), [self.proc["workpiece"], "hand"])
        touched = [r for r in wp if any(_touches(r[:4], h[:4]) for h in hands)] or wp
        if not touched:
            return (0, 0, W, H)
        return tuple(max(touched, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]) * r[4])[:4])

    def _sam(self, frame: np.ndarray, prompt: str) -> list[tuple]:
        """(x1, y1, x2, y2, conf) per match with conf >= 0.5. `prompt` is a noun phrase or box tokens."""
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        url = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
        try:
            r = self.client.respond_stream([{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": url}]}],
                model=SAM, metadata={"include_confidence": "true", "mask_encoding": "one_bit"}, timeout=SAM_TIMEOUT)
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            self.sam_failures += 1      # the tick goes on without a box; the next tick re-prompts from self.box
            return []
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
                while self._latest is None and not self._deliver_due and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                latest, self._latest = self._latest, None
                self._deliver_due = False
            if latest is None:          # a check finished while no frame was waiting
                t = time.time() - self.t0
                try:
                    self.tracker.deliver(t)
                    self._publish_state(t, None, None, None)
                except Exception as e:
                    self.publish({"type": "error", "t": round(t, 2), "message": str(e)[:300]})
                continue
            t, frame = latest
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
        else:
            self.tracker.deliver(t)
        self._publish_state(t, p, sim, box, frame=frame, timing=(t_start, t_sam, t_model))

    def _publish_state(self, t: float, p, sim, box, frame: np.ndarray | None = None, timing=None) -> None:
        events = []
        for e in self.tracker.drain():
            ev = {"t": e.t, "kind": e.kind, "step": e.step, **e.data}
            if e.kind == "check":
                self.cost += e.data.get("cost") or 0.0
            if e.kind == "instruct":
                ev["play"] = self._say_step(e.step)
            if e.kind == "warn":
                ev["play"] = self._say_warn(e.step, e.data.get("what") or "", e.data.get("utterance_id"))
            events.append(ev)
        if not events and frame is None:
            return                      # nothing new to say between frames
        mirror_t = self.model.mirror(p, self.expert) if p is not None else None
        t_start, t_sam, t_model = timing or (time.time(),) * 3
        self.publish({"type": "state", "t": round(t, 2), "progress": p, "similarity": sim, "box": box,
                      "frame_size": [frame.shape[1], frame.shape[0]] if frame is not None else list(self._size or (0, 0)),
                      "step": self.tracker.k,
                      "finished": self.tracker.finished, "mirror_t": mirror_t, "events": events,
                      "tick_ms": round(1000 * (time.time() - t_start)), "cost_usd": round(self.cost, 4),
                      "frames_in": self.frames_in, "frames_gated": self.frames_gated,
                      "photos": self.photos_used, "photos_failed": self.photos_failed, "sam_failures": self.sam_failures,
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

    def _cloned(self, name: str) -> str | None:
        """A line pre-rendered in the expert's own cloned voice (procedures/<name>/voice/<name>.wav),
        made ahead of time because cloning takes minutes per line on the laptop. English only."""
        if self.lang != "en":
            return None
        f = self.dir / "voice" / f"{name}.wav"
        return f"/procedures/{self.name}/voice/{name}.wav" if f.exists() else None

    def _localized(self, name: str) -> str | None:
        """The same line in the novice's language (compile/localize.py renders clips/<lang>/)."""
        if self.lang == "en":
            return None
        f = self.dir / "clips" / self.lang / f"{name}.wav"
        return f"/procedures/{self.name}/clips/{self.lang}/{name}.wav" if f.exists() else None

    def _say_step(self, k: int) -> dict | None:
        """The step's instruction: in the novice's language if localized, else the expert's cloned
        voice, else the synthetic one. The caption is the expert's recorded line (translated when
        localized)."""
        st = self.proc["steps"][k]
        caption = st["voice"][0]["text"] if st["voice"] else st["instruction"]
        if self.local:
            caption = self._local_steps[st["id"]]["instruction"]
        url = (self._localized(f"{st['id']}_say") or self._cloned(f"{st['id']}_say")
               or (f"/procedures/{self.name}/{st['say']['file']}" if st.get("say") else None))
        if url:
            return {"url": url, "text": caption}
        return self._clip(st["voice"][0]) if st["voice"] else None

    def _say_warn(self, k: int, what: str, utterance_id: str | None) -> dict:
        """A warning: the pre-rendered cloned line for the matching known mistake when Spark's
        description matches one, else a generic cloned line, else synthesized on the spot."""
        st = self.proc["steps"][k]
        advice = self.voice[utterance_id]["text"] if utterance_id in self.voice else ""
        words = set(re.findall(r"[a-z]+", what.lower()))
        best, best_n = None, 0
        for j, m in enumerate(st["known_mistakes"]):
            n = len(words & set(re.findall(r"[a-z]+", m.lower())) - {"the", "a", "of", "to", "not", "and"})
            if n > best_n:
                best, best_n = j, n
        # Synthesis takes about a second and would hold the tick thread, so warnings use lines
        # rendered at session start: the matching known mistake, else the generic one
        key = f"{st['id']}_warn{best + 1}" if best is not None and best_n >= 2 else "generic_warn"
        text = advice or what
        if self.local and best is not None and best_n >= 2:
            text = self._local_steps[st["id"]]["known_mistakes"][best]
        url = self._localized(key) or self._cloned(key) or (self._localized("generic_warn") if self.local else None)
        if url:
            return {"url": url, "text": text}
        i = self._spoken_ids.get(key) or self._spoken_ids.get("generic_warn")
        return {"url": f"/api/spoken/{i}.wav" if i else None, "text": text}

    def _prerender(self) -> None:
        """MMS-TTS lines for every known mistake plus a generic Stop, made while the first frames
        arrive; used when no cloned line exists for the warning."""
        sp = speaker()
        sp.warm()
        lines = {"generic_warn": "Stop. Check this against the expert's work before you go on."}
        for st in self.proc["steps"]:
            for j, m in enumerate(st["known_mistakes"]):
                lines[f"{st['id']}_warn{j + 1}"] = f"Stop. It looks like you're {m}. " + (
                    st["why"].capitalize() + "." if st["why"] else "Compare it with the expert's.")
        for key, text in lines.items():
            if self._stop:
                return
            i = len(self.spoken) + 1
            self.spoken[i] = sp.say(text)
            self._spoken_ids[key] = i

    def request_check(self) -> None:
        """The novice asked for a check: take a fresh still and ask Spark on the next frame."""
        self._photo_last = -1e9
        self.tracker.asked_for = True
