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
import io
import json
import os
import re
import subprocess
import threading
import time
import struct
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import av
import cv2
import httpx
import numpy as np
from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt

from apprentice.capture.frame_gate import LiveGate
from apprentice.compile.lines import say_line, warn_line
from apprentice.guide.policy import Check, SparkPolicy
from apprentice.guide.recorder import Recorder
from apprentice.guide.state import Config, Tracker, step_ranges
from apprentice.models.meta import SAM, SPARK as SPARK_MODEL, MetaClient
from apprentice.models.speech import speaker
from apprentice.progress.crop import _touches, crop, parse_lines
from apprentice.progress.embed import Embedder
from apprentice.progress.fuse import TaskProgress
from apprentice.progress.model import ProgressModel

ROOT = Path(__file__).resolve().parents[3]
SMOOTH = 5
REACQUIRE = 10
PHOTO_MIN_GAP_S = 6.0   # each still freezes the stream for 2-3 s, so most ticks must not ask for one
PHOTO_TIMEOUT_S = 6.0   # the phone holds a still up to 2.5 s for the voice line, then 2-3 s to capture
STILL_FRESH_S = 10.0    # a check asked soon after a still may judge the same still
TARGET_REFIND_S = 15.0      # Spark re-finds the target this often, so a SAM track cannot drift for long
TARGET_MIN_FRAC = 0.06      # a target narrower than this fraction of the photo is too far away to judge
REMIND_QUIET_S = 6.0        # no reminder within this long of a hand on the work: they are busy, not stuck
REMIND_S = float(os.environ.get("REMIND_S", "20"))   # repeat the step after this much silence; 0 turns it off
ASK_TIMEOUT_S = 20.0    # a spoken answer slower than this fails instead of leaving the phone waiting
SAM_TIMEOUT = httpx.Timeout(6.0, connect=3.0)   # one slow SAM call must not stall the guide (17 s seen live)
DASH_FPS, DASH_H = 8, 640   # dashboard frames: thinned and downscaled before the base64 fan-out
# "Hands off" is the signal that a step is done: no hand over the paper on two looks in a row.
# Then a still is taken and judged, as if Check was tapped.
SETTLE_HAND_S = 1.5         # how often SAM looks for hands while the paper is in view
SETTLE_TICKS = 2            # clear looks in a row (about 1.5-3 s)
SETTLE_COOLDOWN_S = 8.0     # between hands-off checks until a hand touches the paper again
SETTLE_REARM_S = 20.0       # hands stay off this long after a settle check: look again (the novice
                            # finished and is waiting for the advance, not pausing mid-work)
SPEND_CAP_USD = float(os.environ.get("SPEND_CAP_USD", "40"))   # across all sessions of this server


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
        self.task = TaskProgress(self.proc, self.model, self.model.runs[0])  # the compiled (narrated) run
        self.shown: float | None = None
        self.client = client or MetaClient()
        self.policy = SparkPolicy(self.dir, self.client)
        self.embedder = embedder
        self.pool = ThreadPoolExecutor(4)
        # A procedure can lower the confidence a step check needs (procedure.json "done_conf"):
        # a crowded scene like a wiring rack keeps Spark's answers right but less sure
        cfg = Config(done_conf=float(self.proc.get("done_conf", Config.done_conf)),
                     signal=self.proc.get("advance_on") == "signal")
        self.tracker = Tracker(step_ranges(self.proc), self._ask, cfg,
                               throw_last=self.proc["steps"][-1].get("final_action") == "throw")
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
        self._intro_said = False
        self._varied: dict[str, int] = {}
        self._hands_on_t = -1e9      # last time a hand was on the work (no reminders while they work)
        self._still_raw: np.ndarray | None = None
        self._find_fut: Future | None = None
        self._find_t = -1e9
        self._seen_imgs: tuple = (None, None, None)
        self._hold_until = 0.0
        self._said_t: float | None = None     # last time a line was spoken, for the reminder
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
        self._last_view: tuple[float, np.ndarray] | None = None    # (t, cropped stream frame) for questions
        self._deliver_due = False      # a check finished; hand it out even if no frame arrives
        self._dash_t = -1e9
        self._settle = {"box": None, "crop": None, "ticks": 0, "fired": -1e9, "armed": True, "blocked": 0, "diff": None}
        self._goto: int | None = None
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
        if self._stop:                  # a replaced device connection closes its session twice
            return
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
            # Only a check the novice asked for (hands off, tap) takes a fresh still: each still waits
            # for the voice line, then freezes the stream 2-3 s, which made every check answer 10-15 s
            # after it was asked (catch5). Timed checks judge the 720p stream frame, about 4 s. A
            # mistake check judges the still just taken when there is one.
            asked_for = (kind == "done" or self.tracker.cfg.signal) and t == self.tracker.manual_at
            if asked_for and self.tracker.cfg.signal:
                still = None        # signal mode: judge the frame at the signal; a still added ~5 s per step (novice7)
            elif asked_for:
                still = self._still()
            elif kind == "done":
                still = None
            else:
                cached = self._still_view
                still = cached[1] if cached and time.time() - cached[0] < STILL_FRESH_S else None
            if self._stop:
                raise RuntimeError("session closed")
            view = frame if still is None else still
            if kind == "mistake" and asked_for and self.proc.get("target") and self.proc["steps"][step].get("inspect"):
                res = self._target_check(step, self._still_raw if still is not None else frame)
                res.source = "stream" if still is None else "photo"
                self._publish_seen(t, step, res, *self._seen_imgs)
                return res
            res = self.policy.locate(view) if kind == "done" else self.policy.mistake(step, view)
            if kind == "mistake" and asked_for and self.tracker.cfg.signal:
                self._publish_seen(t, step, res, view, None, None)
            if kind == "mistake" and res.yes:
                res = confirm_turned(res, lambda: self.policy.mistake(step, cv2.rotate(view, cv2.ROTATE_90_CLOCKWISE)))
            res.source = "stream" if still is None else "photo"
            return res
        fut = self.pool.submit(run)

        def wake(_):
            with self._cv:
                self._deliver_due = True
                self._cv.notify()
        fut.add_done_callback(wake)
        return _FutureHandle(fut)

    def _publish_seen(self, t: float, step: int, res: Check, img: np.ndarray | None, box, crop) -> None:
        """"What Spark saw" for the dashboard: the photo judged at a done-signal with the target box
        drawn on it, the crop the check ran on, and the verdict. Recorded, so replays show it too."""
        def url(im, w):
            if im is None:
                return None
            im = cv2.resize(im, (w, round(im.shape[0] * w / im.shape[1])))
            return "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 80])[1]).decode()
        shot = None
        if img is not None:
            shot = img.copy()
            if box is not None:
                cv2.rectangle(shot, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), (245, 211, 125), max(3, shot.shape[1] // 160))
        status = ("unclear" if res.unclear else "mistake" if res.yes and res.confidence >= self.tracker.cfg.signal_mistake_conf
                  else "ok")
        msg = {"type": "seen", "t": round(t, 2), "step": step, "status": status, "verdict": res.reason,
               "conf": round(res.confidence, 2), "check": self.proc["steps"][step].get("inspect", ""),
               "photo": url(shot, 420), "crop": url(crop, 320)}
        if self.recorder:
            self.recorder.line(msg)
        self.publish(msg)

    def _target_check(self, step: int, img: np.ndarray) -> Check:
        """A done-signal at a step with an "inspect" line: judge only a clear view of the target.
        Spark finds the target on the photo; hidden, too small, or under a hand is an unclear view
        (the guide asks the novice to show it), else the mistake check runs on a tight crop of it."""
        H, W = img.shape[:2]
        self._seen_imgs = (img, None, None)
        # the tracked box is the target only when the target is tracked continuously ("track_target")
        tracked = self.proc.get("track_target") and self.box is not None
        box, cost = (self._still_box(img), 0.0) if tracked else (None, 0.0)
        if box is None:
            box, cost = self.policy.find_target(img)
        if box is None:
            return Check(False, 0.0, "can't see the connector", 0.0, cost, unclear=True)
        x1, y1, x2, y2 = box
        if (x2 - x1) < TARGET_MIN_FRAC * W:
            return Check(False, 0.0, "connector too small in view", 0.0, cost, unclear=True)
        hands = [h for h in self._sam(img, "hand") if h[4] >= 0.6]
        self._seen_imgs = (img, box, None)
        if any(_touches(box, h[:4], margin=0.0) for h in hands):
            return Check(False, 0.0, "a hand is on the connector", 0.0, cost, unclear=True)
        pad = 0.6 * max(x2 - x1, y2 - y1)
        a, b, c, d = int(max(0, x1 - pad)), int(max(0, y1 - pad)), int(min(W, x2 + pad)), int(min(H, y2 + pad))
        crop = img[b:d, a:c]
        crop = cv2.resize(crop, (640, round(crop.shape[0] * 640 / crop.shape[1])), interpolation=cv2.INTER_CUBIC)
        if self.recorder:
            self.recorder.photo(time.time() - self.t0, cv2.imencode(".jpg", crop)[1].tobytes())
        self._seen_imgs = (img, (x1, y1, x2, y2), crop)
        res = self.policy.mistake(step, crop)
        res.cost += cost
        return res

    # -- stills: the stream tracks the work, Spark judges it on a sharper picture when it can -----

    def on_photo(self, photo_id: int, jpeg: bytes | None) -> None:
        got = self._photo_got
        if got and photo_id == self._photo_id:
            got[1].append(jpeg)
            got[0].set()

    def _still(self) -> np.ndarray | None:
        """A fresh still from the glasses cropped to the workpiece, or None to use the stream frame
        (photos off, or the capture failed). A still taken moments ago, or one in flight for another
        check, is shared rather than skipped, so a hands-off check never falls back to a blurry
        stream frame because a timed check got to the camera first."""
        if not self.photos_on:
            return None
        if time.time() - self._photo_last < PHOTO_MIN_GAP_S:
            cached = self._still_view
            return cached[1] if cached and time.time() - cached[0] < STILL_FRESH_S else None
        if not self._photo_lock.acquire(blocking=False):
            if not self._photo_lock.acquire(timeout=PHOTO_TIMEOUT_S + 6):   # wait for the one in flight
                return None
            self._photo_lock.release()
            cached = self._still_view
            return cached[1] if cached and time.time() - cached[0] < STILL_FRESH_S else None
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
            self._still_raw = img
            sb = self._still_box(img)
            view = self._view(img, sb) if sb is not None else img
            self._still_view = (time.time(), view)
            return view
        finally:
            self._photo_lock.release()

    def _still_box(self, img: np.ndarray) -> tuple | None:
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
        if self.proc.get("target") and self.proc.get("track_target"):
            return None        # target mode: the name search finds the wrong thing; the caller decides
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

    def _target_box(self, frame: np.ndarray) -> tuple | None:
        """Target mode (procedure.json "target"): Muse Spark finds the part to watch (it knows what
        "the connector the wires go into" is; SAM finds nothing by that name), SAM 3.1 follows it
        from its last box every tick. Spark runs in the background: at the start, whenever the track
        is lost, and every TARGET_REFIND_S to pull a drifting track back."""
        now = time.time()
        f = self._find_fut
        if f is not None and f.done():
            self._find_fut = None
            try:
                found, cost = f.result()
            except Exception:
                found, cost = None, 0.0
            self.cost += cost
            if found is not None:
                fresh = self._follow(frame, found) or found       # the head moved during the ~10 s search
                if self.box is None or _iou(self.box, fresh) < 0.3:
                    self.box = fresh
            else:
                # Spark cannot see the target: a SAM box prompt still returns a box where it was
                # told to look (seen on black frames), so the track is dropped, not kept
                self.box = None
        box = self._follow(frame, self.box) if self.box is not None else None
        if box is None:
            self.box = None
        if box is not None and not _plausible(self.box, box):
            box = None
        if self._find_fut is None and ((box is None and now - self._find_t > 2.0) or now - self._find_t > TARGET_REFIND_S):
            self._find_t = now
            self._find_fut = self.pool.submit(self.policy.find_target, frame.copy())
        return box

    def _follow(self, frame: np.ndarray, box: tuple) -> tuple | None:
        """SAM 3.1 with a box prompt: the same object near `box` in this frame."""
        H, W = frame.shape[:2]
        x1, y1, x2, y2 = (int(v) for v in box)
        prompt = build_object_prompt(size=(W, H), objects=[PromptObject(
            id=1, prompts=[FramePrompt(frame=0, box=PromptBox(max(0, x1), max(0, y1), min(x2 + 1, W), min(y2 + 1, H)))])])
        got = self._sam(frame, prompt)
        return tuple(got[0][:4]) if got else None

    def _workpiece(self, frame: np.ndarray) -> tuple | None:
        """Follow the last box with a SAM box prompt (stable on blurry frames); when that loses it,
        find the workpiece again by name, keeping the match the hands touch. Re-finding by name every
        REACQUIRE ticks keeps a box-prompt track from drifting onto a hand for good. A procedure with
        a "target" is tracked by _target_box instead."""
        if self.proc.get("target") and self.proc.get("track_target"):
            return self._target_box(frame)
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
        if not touched:
            # Hands off the work is exactly when it should be judged (quick run, Nov 3: 0 of 20 ticks
            # found the paper once the hands came off, so no check ran). Keep following the paper
            # near the last box, else take the most confident match; a hand on something else does
            # not count as touching.
            near = [r for r in wp if self.box is not None and _plausible(self.box, r[:4], min_iou=0.1)]
            touched = near or [max(wp, key=lambda r: r[4])] if wp else []
            if not touched:
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
            if latest is None:          # a check finished, or a step was picked, while no frame was waiting
                t = time.time() - self.t0
                try:
                    if self._goto is not None:
                        k, self._goto = self._goto, None
                        self.tracker.goto(t, k)
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
            view = self._view(frame, box)
            self._last_view = (t, view)
        t_model = time.time()
        if self._goto is not None:
            k, self._goto = self._goto, None
            self.tracker.goto(t, k)
        if box:
            if not self.tracker._throwing():
                self._watch_settle(t, frame, box)
            self.tracker.tick(t, p, sim, view)
        else:
            if self.tracker.cfg.signal and not self.tracker.finished:
                # no workpiece box (step 2 looks at an empty channel: no wires in view, so novice7 waited
                # 26 s): with no box, no hand anywhere in the frame is the done-signal, judged on the frame
                H, W = frame.shape[:2]
                self._watch_settle(t, frame, (0, 0, W, H))
                self.tracker.tick(t, None, None, frame)
            else:
                self.tracker.lost(t)
                self.tracker.deliver(t)
        self._publish_state(t, p, sim, box, frame=frame, timing=(t_start, t_sam, t_model))

    def _publish_state(self, t: float, p, sim, box, frame: np.ndarray | None = None, timing=None) -> None:
        events = []
        for e in self.tracker.drain():
            ev = {"t": e.t, "kind": e.kind, "step": e.step, **e.data}
            if e.kind == "check":
                self.cost += e.data.get("cost") or 0.0
            if e.kind == "instruct":
                if e.step == 0 and not self._intro_said and self.proc.get("intro"):
                    # the expert's introduction to the task, once, ahead of the first step
                    self._intro_said = True
                    events.append({"t": e.t, "kind": "intro", "step": 0, "play": self._say_line("intro", self.proc["intro"])})
                    self._hold_until = e.t + self._line_s("intro")   # no reminder while the intro plays
                    if self.proc.get("presence"):    # the guide is there with them: how to show it a finished step
                        events.append({"t": e.t, "kind": "presence", "step": 0, "play": self._say_line("presence", self.proc["presence"])})
                        self._hold_until += self._line_s("presence")
                ev["play"] = self._say_step(e.step)
            if e.kind == "closer":
                ev["play"] = self._say_line("closer", self.proc.get("closer") or "Hands away, and lean in so I can see the connector.")
                self._settle.update(armed=True, ticks=0)      # the next clear pause is a new signal
            if e.kind == "looking" and self.proc.get("looking"):
                ev["play"] = self._say_varied("looking", self.proc["looking"])
            if e.kind == "step_done" and self.proc.get("good") and e.step + 1 < len(self.proc["steps"]):
                ev["play"] = self._say_varied("good", self.proc["good"])
            if e.kind == "warn":
                ev["play"] = self._say_warn(e.step, e.data.get("what") or "", e.data.get("utterance_id"))
            if e.kind == "complete":
                ev["play"] = self._say_complete(e.t)
            events.append(ev)
        # A novice working hands-free hears the current step again after REMIND_S of silence, so the
        # guide never goes quiet while they wait for a check to land
        if any(e.get("play") for e in events):
            self._said_t = t
        elif (frame is not None and REMIND_S > 0 and not self.tracker.finished and self._said_t is not None
              and t - self._said_t >= REMIND_S and t >= self._hold_until and t - self._hands_on_t >= REMIND_QUIET_S
              and self.tracker.k < len(self.proc["steps"])):
            play = self._say_step(self.tracker.k)
            if play:
                events.append({"t": round(t, 2), "kind": "remind", "step": self.tracker.k, "play": play})
                self._said_t = t
        if not events and frame is None:
            return                      # nothing new to say between frames
        if frame is not None:           # a frame tick: the gauge moves (box or not), events alone don't
            self.shown = self.task.update(t, self.tracker.k, self.tracker.finished, p)
            self.curve.append((round(t, 2), round(self.shown, 4)))
        elif self.tracker.finished:
            self.shown = 1.0
        # The mirror follows the shown progress, so the expert frame is always from the current step
        mirror_t = self.model.mirror(self.task.label_at(self.shown), self.expert) if self.shown is not None else None
        t_start, t_sam, t_model = timing or (time.time(),) * 3
        self.publish({"type": "state", "t": round(t, 2), "progress": self.shown, "progress_raw": p, "similarity": sim, "box": box,
                      "frame_size": [frame.shape[1], frame.shape[0]] if frame is not None else list(self._size or (0, 0)),
                      "step": self.tracker.k,
                      "finished": self.tracker.finished, "mirror_t": mirror_t, "events": events,
                      "tick_ms": round(1000 * (time.time() - t_start)), "cost_usd": round(self.cost, 4),
                      "frames_in": self.frames_in, "frames_gated": self.frames_gated,
                      "photos": self.photos_used, "photos_failed": self.photos_failed, "sam_failures": self.sam_failures,
                      "settle": {"clear_looks": self._settle["ticks"], "blocked": self._settle["blocked"], "hands": self._settle["diff"]},
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
        # The caption is the compiled instruction: the raw transcript line can carry transcription
        # slips ("So I tore off the page from!") that read badly on the phone and in the video
        caption = self._local_steps.get(st["id"], st)["instruction"] if self.local else say_line(st)
        url = (self._localized(f"{st['id']}_say") or self._cloned(f"{st['id']}_say")
               or (f"/procedures/{self.name}/{st['say']['file']}" if st.get("say") and not self._expert_only() else None))
        if url is None and self._expert_only():
            return {"url": None, "text": caption}
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
        if self.local and best is not None and best_n >= 2 and st["id"] in self._local_steps:
            text = self._local_steps[st["id"]]["known_mistakes"][best]
        url = self._localized(key) or self._cloned(key) or (self._localized("generic_warn") if self.local else None)
        if url is None and self._expert_only():
            url = self._cloned("generic_warn")      # never the synthetic voice: the expert's generic stop
        if url or self._expert_only():
            return {"url": url, "text": text}
        i = self._spoken_ids.get(key) or self._spoken_ids.get("generic_warn")
        return {"url": f"/api/spoken/{i}.wav" if i else None, "text": text}

    def _expert_only(self) -> bool:
        """procedure.json "expert_voice_only": the novice only ever hears the expert (recorded or cloned);
        a line with no such audio is shown as text instead of spoken in another voice. English only,
        as the clone is English."""
        return bool(self.proc.get("expert_voice_only")) and self.lang == "en"

    def _say_varied(self, name: str, lines) -> dict:
        """One of several phrasings (voice/<name>_<n>.wav), rotating so the guide never says the same
        thing twice in a row. A plain string is a single line."""
        if isinstance(lines, str):
            return self._say_line(name, lines)
        n = self._varied.get(name, 0)
        self._varied[name] = n + 1
        j = n % len(lines)
        return self._say_line(f"{name}_{j + 1}", lines[j])

    def _line_s(self, name: str) -> float:
        """Length of a pre-rendered voice line in seconds (0 if there is none)."""
        # The cloned lines are 32-bit float WAVs, which the wave module cannot open: read the header
        f = self.dir / "voice" / f"{name}.wav"
        try:
            raw = f.read_bytes()
            i = raw.index(b"fmt ")
            rate, byte_rate = struct.unpack("<II", raw[i + 12:i + 20])
            j = raw.index(b"data")
            return struct.unpack("<I", raw[j + 4:j + 8])[0] / byte_rate
        except Exception:
            return 0.0

    def _say_line(self, name: str, text: str) -> dict:
        """A fixed procedure line (intro, complete): the expert's cloned voice when rendered
        (voice/<name>.wav), else synthesized now."""
        url = self._cloned(name)
        if url:
            return {"url": url, "text": text}
        if self._expert_only():
            return {"url": None, "text": text}      # no other voice: the line shows as text
        try:
            i = len(self.spoken) + 1
            self.spoken[i] = speaker().say(text)
            return {"url": f"/api/spoken/{i}.wav", "text": text}
        except Exception:
            return {"url": None, "text": text}

    def _say_complete(self, t: float) -> dict | None:
        """The wrap-up once the last step lands: the procedure's own closing line in the expert's
        voice when it has one, else well done, the run's stats, and the one thing to remember,
        synthesized on the spot (the stats are only known now)."""
        if self.proc.get("complete"):
            return self._say_line("complete", self.proc["complete"])
        warns = sum(1 for e in self.tracker.events if e.kind == "warn")
        mins = t / 60
        stats = (f"You took {mins:.0f} minute{'s' if mins >= 1.5 else ''}"
                 + (f" and needed {warns} warning{'s' if warns != 1 else ''}." if warns
                    else ", with not a single warning."))
        takeaway = self.proc.get("takeaway")
        text = (f"Good job, you finished the whole task! {stats}"
                + (f" One thing to remember: {takeaway}" if takeaway else ""))
        try:
            i = len(self.spoken) + 1
            self.spoken[i] = speaker().say(text)
            return {"url": f"/api/spoken/{i}.wav", "text": text}
        except Exception:
            return {"url": None, "text": text}     # the caption still lands if synthesis fails

    def _prerender(self) -> None:
        """MMS-TTS lines for every known mistake plus a generic Stop, made while the first frames
        arrive; used when no cloned line exists for the warning."""
        sp = speaker()
        sp.warm()
        lines = {"generic_warn": "Stop. Check this against the expert's work before you go on.",
                 "closer": "I can't see it clearly. Hands away, and lean in so I can see the connector."}
        for st in self.proc["steps"]:
            for j, m in enumerate(st["known_mistakes"]):
                lines[f"{st['id']}_warn{j + 1}"] = warn_line(m, st["why"])
        for key, text in lines.items():
            if self._stop:
                return
            i = len(self.spoken) + 1
            self.spoken[i] = sp.say(text)
            self._spoken_ids[key] = i

    ASK_SCHEMA = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "clip_from": {"type": "integer", "description": "first turn id of the expert's own words that answer the question, or -1"},
            "clip_to": {"type": "integer", "description": "last turn id of that passage (same as clip_from for one turn), or -1"},
            "answer": {"type": "string", "description": "one or two short spoken sentences"},
        },
        "required": ["clip_from", "clip_to", "answer"],
    }

    def ask(self, question: str) -> dict:
        """The novice asked the expert something. Muse Spark answers from the expert's recording
        (steps, their words with turn ids) and what the glasses see now, in the novice's language.
        When a moment of the recording answers the question, Spark names its turns and the reply
        plays that moment in the expert's own voice first, then a short spoken answer about the
        work in front of them (MMS-TTS). Without such a moment, or if the clip cannot be built, it
        is the spoken answer alone. Returns {question, answer, url, step, clip?}."""
        k = self.tracker.k
        st = self.proc["steps"][k]
        turns = [(v["turn_id"], i, v) for i, s_ in enumerate(self.proc["steps"]) for v in s_["voice"]]
        said = "\n".join(f"[{tid}] step {i + 1}, {v['start']:.0f}s: {v['text']}" for tid, i, v in turns)
        steps = "\n".join(f"{i + 1}. {s_['title']}: {s_['instruction']} (done when: {s_['done_check']})" for i, s_ in enumerate(self.proc["steps"]))
        lang_note = f" Write the answer in {self.local['language']}." if self.local else ""
        text = (f"You are the expert who recorded this procedure, answering an apprentice who is doing it now.\n"
                f"Task: {self.proc['task']}.\nSteps:\n{steps}\nThe apprentice is on step {k + 1}: {st['instruction']}\n"
                f"Known mistakes on this step: {'; '.join(st['known_mistakes']) or 'none'}\n"
                f"What the expert said while recording, as numbered turns:\n{said}\n\n"
                f"The apprentice asks: \"{question}\"\n"
                "If some consecutive turns of the expert's own words answer this, give their first and last turn ids "
                "(at most about 12 seconds of speech; prefer the current step). Choose the shortest passage that "
                "states the answer cleanly: skip turns with slips, self-corrections or asides (\"sorry\", \"oh wow\"), "
                "and turns that describe doing it wrong. That recording is played to the "
                "apprentice first, in the expert's voice. Then write the answer: one short spoken sentence when you give "
                "turns (two at most without them), "
                "concrete and practical, that add what the picture shows or what to do next, without repeating the "
                "recording word for word. If no turns answer it, use -1 for both ids and answer from the steps and "
                f"the picture. Do not invent steps the expert did not record.{lang_note}")
        content = [{"type": "text", "text": text}]
        latest = self._still_view if self._still_view and time.time() - self._still_view[0] < STILL_FRESH_S else self._last_view
        if latest is not None:
            ok, buf = cv2.imencode(".jpg", latest[1], [cv2.IMWRITE_JPEG_QUALITY, 80])
            content += [{"type": "text", "text": "The apprentice's work right now:"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}}]
        fmt = {"type": "json_schema", "json_schema": {"name": "ask", "strict": True, "schema": self.ASK_SCHEMA}}
        latency = 0.0
        out = None
        # Reasoning tokens count against max_tokens: at 400 with "low" the answer came back empty
        for parts in (content, content[:1]):       # retry without the picture if the first answer is empty
            r = self.client.chat([{"role": "user", "content": parts}], model=SPARK_MODEL, max_tokens=2000,
                                 reasoning_effort="minimal", timeout=ASK_TIMEOUT_S, response_format=fmt)
            latency += r.latency_s
            self.cost += (r.usage.get("prompt_tokens", 0) * 1.25 + r.usage.get("completion_tokens", 0) * 4.25) / 1e6
            try:
                out = json.loads(r.text)
            except (json.JSONDecodeError, TypeError):
                out = None
            if out and out.get("answer", "").strip():
                break
        answer = (out or {}).get("answer", "").strip()
        reply = speaker(self.lang).say(answer)
        clip = self._expert_clip((out or {}).get("clip_from", -1), (out or {}).get("clip_to", -1))
        if not clip and self._expert_only():
            # nothing the expert recorded answers it: say so in the expert's voice, the answer goes on screen
            f = self.dir / "voice" / "not_covered.wav"
            reply = f.read_bytes() if f.exists() else b""
        if clip and (self.proc.get("ask_clip_only") or self._expert_only()) and self.lang == "en":
            # the expert's recorded words are the answer; a synthesized sentence after them is a second,
            # different voice (novice7). The written answer still goes to the screens.
            try:
                reply = _pcm_wav(clip["audio"])
                clip_only = True
            except Exception:
                clip_only = False
        else:
            clip_only = False
        if clip and not clip_only:
            try:
                reply = _join_wavs(clip["audio"], reply)
            except Exception as e:          # the spoken answer alone is still a full reply
                print(f"ask: could not join the expert clip: {e}", flush=True)
                clip = None
        i = len(self.spoken) + 1
        self.spoken[i] = reply
        res = {"question": question, "answer": answer, "url": f"/api/spoken/{i}.wav", "step": k,
               "latency_s": round(latency, 2), "model": r.model}
        if clip:
            res["clip"] = {k_: clip[k_] for k_ in ("text", "start", "end", "turns", "step")}
        return res

    def _expert_clip(self, first: int, last: int) -> dict | None:
        """The expert's own recorded words for turns first..last, from the clips cut at compile time
        (procedures/<name>/clips/), as 16 kHz mono PCM. None when the ids are not a short run of
        recorded turns."""
        if first is None or last is None or first < 0 or last < first or last - first > 6:
            return None
        vs = [self.voice[t] for t in range(first, last + 1) if t in self.voice]
        if not vs or sum(v["end"] - v["start"] for v in vs) > 15.0:   # speech only: pauses between turns are not in the clips
            return None
        pcm = []
        for v in vs:
            f = self.dir / v["file"]
            if not f.exists():
                return None
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                                 capture_output=True, check=True).stdout
            pcm.append(np.frombuffer(raw, dtype=np.int16))
        step = next((i for i, s_ in enumerate(self.proc["steps"]) for v in s_["voice"] if v["turn_id"] == first), None)
        return {"audio": np.concatenate(pcm), "text": " ".join(v["text"] for v in vs).strip(),
                "start": vs[0]["start"], "end": vs[-1]["end"], "turns": [first, last], "step": step}

    def request_check(self, trigger: str = "tap") -> None:
        """A check was asked for (the phone's button, or the novice taking their hands off): take a
        fresh still and ask Spark on the next frame."""
        self._photo_last = -1e9
        self.tracker.trigger = trigger
        self.tracker.asked_for = True

    def request_goto(self, k: int) -> None:
        """The novice picked a step on the phone; applied on the worker thread."""
        with self._cv:
            self._goto = k
            self._deliver_due = True
            self._cv.notify()

    def _watch_settle(self, t: float, frame: np.ndarray, box: tuple) -> None:
        """Ask for a check when no hand is on the work. The camera is on the novice's head, so the
        picture never holds still (catch2: the frame changed by 10-50 grey levels between ticks
        while the hands were off); whether a hand is over the paper is the signal instead. SAM looks
        for hands every SETTLE_HAND_S; two clear looks in a row fire one check, and a hand coming
        back re-arms it."""
        st = self._settle
        if t < self._hold_until:                     # still introducing the task: not a done-signal yet
            return
        if st.get("step") != self.tracker.k:        # a new step: the next pause counts
            st.update(step=self.tracker.k, armed=True, ticks=0, fired=-1e9)
        if t - st.get("hand_t", -1e9) < SETTLE_HAND_S:
            return
        st["hand_t"] = t
        hands = [h for h in self._sam(frame, "hand") if h[4] >= 0.6]
        on_paper = any(_touches(box, h[:4], margin=0.0) for h in hands)
        st["diff"] = len(hands)
        if self.proc.get("track_target") and (box[2] - box[0]) < TARGET_MIN_FRAC * frame.shape[1]:
            st["ticks"] = 0                                  # too far away to judge: not a signal
            if not on_paper and t - st.get("closer_t", -1e9) > 15.0:
                st["closer_t"] = t
                self.tracker._emit(t, "closer", self.tracker.k, what="too far from the connector")
            return
        if on_paper:
            self._hands_on_t = t
            st["ticks"], st["armed"] = 0, True
            st["blocked"] += 1
            return
        st["ticks"] += 1
        if not st["armed"] and t - st["fired"] >= SETTLE_REARM_S:
            # thermocouple novice3: the one settle check answered wrong, then the novice stood
            # hands-off and done for 40 s with no recheck, because only a hand back on the work
            # re-arms. A long clear wait means "still waiting", so take another look.
            st["armed"] = True
        if st["ticks"] < SETTLE_TICKS or not st["armed"] or t - st["fired"] < SETTLE_COOLDOWN_S:
            return
        st["fired"], st["armed"] = t, False
        self.publish({"type": "settled", "t": round(t, 2), "step": self.tracker.k})
        if self.tracker.cfg.signal and not self.tracker.finished and t - self.tracker.entered_t >= self.tracker.cfg.signal_min_s:
            self.tracker._emit(t, "looking", self.tracker.k)
        self.request_check("settle")


def _pcm_wav(pcm: np.ndarray, rate: int = 16000) -> bytes:
    """The expert's 16 kHz recording as a WAV, levelled like _join_wavs levels it."""
    clip = pcm.astype(np.float32)
    peak = float(np.abs(clip).max() or 1.0)
    clip = clip * min(4.0, 0.9 * 32767 / peak)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(clip.astype(np.int16).tobytes())
    return buf.getvalue()


def _join_wavs(expert_pcm: np.ndarray, answer_wav: bytes, gap_s: float = 0.45) -> bytes:
    """The expert's recorded words, a short pause, then the spoken answer, as one WAV at the
    answer's sample rate (the phone and dashboard play a single URL)."""
    with wave.open(io.BytesIO(answer_wav)) as f:
        rate = f.getframerate()
        ans = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    clip = expert_pcm.astype(np.float32)
    if rate != 16000:                       # resample the 16 kHz clip to the answer's rate
        n = int(len(clip) * rate / 16000)
        clip = np.interp(np.linspace(0, len(clip) - 1, n), np.arange(len(clip)), clip)
    peak = float(np.abs(clip).max() or 1.0)
    clip = clip * min(4.0, 0.9 * 32767 / peak)      # level the phone recording up to 90% peak, at most 4x
    gap = np.zeros(int(gap_s * rate), dtype=np.int16)
    pcm = np.concatenate([clip.astype(np.int16), gap, ans])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())
    return buf.getvalue()



def confirm_turned(res: Check, turned: Callable[[], Check]) -> Check:
    """A positive mistake answer is kept only if Spark gives it again on the same picture turned a
    quarter turn. A wrong fold is wrong at any angle; an answer that disappears when the picture is
    turned was about orientation. catch5 at 0:51: a correct lengthwise strip lying sideways was
    called "folded crosswise" 3 of 3 times as captured and 0 of 3 times turned upright. The second
    call happens only on positive answers, which are rare."""
    try:
        again = turned()
    except Exception:
        return res                      # no second opinion: keep the first answer
    if again.yes:
        res.cost += again.cost
        res.latency_s += again.latency_s
        return res
    return Check(False, again.confidence, f"turned picture disagrees: {res.reason}", res.latency_s + again.latency_s,
                 res.cost + again.cost, None, source=res.source, model=res.model)
