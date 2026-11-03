"""Record a live session: the glasses' stream exactly as received, plus what the guide did.

out/sessions/<YYYYmmdd-HHMMSS>_<run>/
  stream.hevc     raw Annex B stream, one access unit per phone message
  stream.mp4      the same stream remuxed (no re-encode) at the phone's reported frame rate
  frames.jsonl    arrival time (s since session start) and size of each access unit
  ticks.jsonl     every state the dashboard received (progress, box, step, events, timing)
  meta.json       procedure, run name, stream size, start time, and a summary at the end
  photos/         stills taken for Spark checks, named by arrival time in ms
  label.json      a stub for eval/labels: fill in step_done times and mistake windows

So every live run doubles as a replayable recording and a new eval case.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STREAM_FPS = 7


class Recorder:
    def __init__(self, procedure: str, run: str | None, n_steps: int):
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", run or "run").strip("-") or "run"
        self.dir = ROOT / "out" / "sessions" / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.procedure, self.run, self.n_steps = procedure, safe, n_steps
        self.meta = {"procedure": procedure, "run": safe, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "stream_fps": STREAM_FPS}
        self._hevc = open(self.dir / "stream.hevc", "wb")
        self._frames = open(self.dir / "frames.jsonl", "w")
        self._ticks = open(self.dir / "ticks.jsonl", "w")
        self._lock = threading.Lock()
        self._units = 0
        self._last_state: dict | None = None
        self._events: list[dict] = []
        self._write_meta()

    def config(self, width: int, height: int, fps: int | None = None) -> None:
        self.meta.update(width=width, height=height)
        if fps:
            self.meta["stream_fps"] = fps
        self._write_meta()

    def unit(self, t: float, data: bytes) -> None:
        with self._lock:
            if self._hevc.closed:
                return
            self._hevc.write(data)
            self._frames.write(json.dumps({"t": round(t, 3), "bytes": len(data)}) + "\n")
            self._units += 1

    def photo(self, t: float, jpeg: bytes) -> None:
        d = self.dir / "photos"
        d.mkdir(exist_ok=True)
        (d / f"{round(t * 1000):07d}.jpg").write_bytes(jpeg)

    def state(self, msg: dict) -> None:
        with self._lock:
            if self._ticks.closed:
                return
            self._ticks.write(json.dumps(msg) + "\n")
            self._last_state = msg
            self._events += [e for e in msg.get("events", []) if e["kind"] != "check"]

    def close(self) -> None:
        with self._lock:
            for f in (self._hevc, self._frames, self._ticks):
                f.close()
        last = self._last_state or {}
        self.meta.update(
            ended=time.strftime("%Y-%m-%dT%H:%M:%S"), access_units=self._units,
            duration_s=round(self._units / self.meta["stream_fps"], 1), cost_usd=last.get("cost_usd"),
            steps_done=[[e["t"], e["step"] + 1] for e in self._events if e["kind"] == "step_done"],
            instructions=[[e["t"], e["step"] + 1, e.get("play", {}).get("text")] for e in self._events
                          if e["kind"] == "instruct"],
            warnings=[[e["t"], e["step"] + 1, e.get("what")] for e in self._events if e["kind"] == "warn"],
            complete=any(e["kind"] == "complete" for e in self._events))
        self._write_meta()
        (self.dir / "label.json").write_text(json.dumps({
            "procedure": self.procedure,
            "video": str((self.dir / "stream.mp4").relative_to(ROOT)),
            "labeled_by": "",
            "notes": "",
            "step_done": [None] * self.n_steps,
            "mistakes": [],
        }, indent=1))
        if self._units:
            threading.Thread(target=self._remux, daemon=True).start()

    def _remux(self) -> None:
        # No hvc1 tag: the phone repeats parameter sets in band, and an hvc1 track decodes with errors at 720p
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-r", str(self.meta["stream_fps"]), "-f", "hevc", "-i",
                        str(self.dir / "stream.hevc"), "-c", "copy", str(self.dir / "stream.mp4")],
                       check=False)

    def _write_meta(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta, indent=1))
