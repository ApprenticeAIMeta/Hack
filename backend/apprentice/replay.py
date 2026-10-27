"""Run the guide on a recorded clip and write its timeline.

    python -m apprentice.replay airplane ../footage/plane_novice_7fps.mp4 --name novice

Frames are taken at 2 fps, each scored by the progress model, and fed to the step tracker with
video time as the clock; Spark checks are real calls whose latency delays their results as it
would live. Spark answers are cached per (check, step, frame) in out/replay/<name>/checks.json,
so re-running with different tracker settings is free. Writes out/replay/<name>/timeline.json.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from apprentice.guide.policy import Check, SparkPolicy
from apprentice.guide.state import Config, SimAsker, Tracker, step_ranges
from apprentice.progress.align import score
from apprentice.progress.model import ProgressModel

ROOT = Path(__file__).resolve().parents[2]


class CachedPolicy:
    def __init__(self, policy: SparkPolicy, path: Path):
        self.policy, self.path = policy, path
        self.cache = json.loads(path.read_text()) if path.exists() else {}
        self.frame_i = 0
        self.new_calls = 0

    def _get(self, kind: str, k: int, frame: np.ndarray) -> Check:
        key = f"{kind}:{k}:{self.frame_i}"
        if key not in self.cache:
            res = self.policy.locate(frame) if kind == "locate" else self.policy.mistake(k, frame)
            self.cache[key] = asdict(res)
            self.new_calls += 1
            self.path.write_text(json.dumps(self.cache))
        return Check(**self.cache[key])

    def locate(self, frame):
        return self._get("locate", 0, frame)

    def mistake(self, k, frame):
        return self._get("mistake", k, frame)


def spark_views(video: Path, ticks: np.ndarray, boxes: list, proxy_w: int, pad: float = 0.3,
                out_w: int = 640) -> dict[int, np.ndarray]:
    """What a check sees for each tick: the sharpest source frame of the preceding half second
    (frame gate), cropped around the workpiece box and enlarged. The 2 fps proxy is a second
    re-encode of an already-compressed stream, too degraded for Spark to judge."""
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 7.0
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 0.5
    best: dict[int, tuple[float, np.ndarray]] = {}
    j = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        i = int(np.ceil(j / fps / step - 1e-6))
        j += 1
        if i >= len(ticks):
            break
        sharp = cv2.Laplacian(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        if i not in best or sharp > best[i][0]:
            best[i] = (sharp, f)
    cap.release()
    views = {}
    for i, (_, f) in best.items():
        if boxes[i] is None:
            continue
        H, W = f.shape[:2]
        sc = W / proxy_w
        x1, y1, x2, y2 = (v * sc for v in boxes[i])
        side = max(x2 - x1, y2 - y1) * (1 + 2 * pad)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        a, b = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        c, d = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
        crop = f[b:d, a:c]
        views[i] = cv2.resize(crop, (out_w, round(crop.shape[0] * out_w / crop.shape[1])), interpolation=cv2.INTER_CUBIC)
    return views


def replay(procedure: str, video: Path, name: str, cfg: Config | None = None, end: float | None = None) -> dict:
    pdir = ROOT / "procedures" / procedure
    proc = json.loads((pdir / "procedure.json").read_text())
    model = ProgressModel.load(pdir / "progress.npz")
    s = score(video, name, model, proc["workpiece"])
    out = ROOT / "out" / "replay" / name
    out.mkdir(parents=True, exist_ok=True)
    pol = CachedPolicy(SparkPolicy(pdir), out / "checks_v2.json")
    tr = Tracker(step_ranges(proc), SimAsker(pol.locate, pol.mistake), cfg)
    views = spark_views(video, s["t"], s["boxes"], s["frames"][0].shape[1])

    for i, t in enumerate(s["t"]):
        if end is not None and t > end:
            break
        if s["crops"][i] is None or i not in views:   # workpiece not in view: nothing to judge
            continue
        pol.frame_i = i
        p, sim = s["progress"][i], s["sim"][i]
        tr.tick(float(t), None if np.isnan(p) else float(p), None if np.isnan(sim) else float(sim), views[i])
    tr.flush(float(s["t"][-1]))

    voice = {v["turn_id"]: v for st in proc["steps"] for v in st["voice"]}
    timeline = []
    for e in tr.events:
        row = {"t": e.t, "kind": e.kind, "step": e.step, "title": proc["steps"][e.step]["title"], **e.data}
        if e.kind == "instruct" and proc["steps"][e.step]["voice"]:
            row["play"] = proc["steps"][e.step]["voice"][0]
        if e.kind == "warn" and e.data.get("utterance_id") in voice:
            row["play"] = voice[e.data["utterance_id"]]
        timeline.append(row)
    checks = [e for e in timeline if e["kind"] == "check"]
    result = {"procedure": procedure, "run": name, "events": timeline,
              "summary": {"steps_done": [e["t"] for e in timeline if e["kind"] == "step_done"],
                          "complete": any(e["kind"] == "complete" for e in timeline),
                          "warnings": [e["t"] for e in timeline if e["kind"] == "warn"],
                          "checks": len(checks), "new_spark_calls": pol.new_calls,
                          "check_cost_usd": round(sum(e["cost"] for e in checks), 4),
                          "mean_check_latency_s": round(float(np.mean([e["latency_s"] for e in checks])), 2) if checks else None}}
    (out / "timeline.json").write_text(json.dumps(result, indent=1))
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the guide on a recorded clip")
    ap.add_argument("procedure")
    ap.add_argument("video", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--end", type=float, default=None, help="stop at this many seconds")
    a = ap.parse_args()
    r = replay(a.procedure, a.video.resolve(), a.name, end=a.end)
    for e in r["events"]:
        if e["kind"] == "check":
            mark = "?? " if e.get("unclear") else (f"n={e['completed']}" if e["check"] == "done" else ("yes" if e["yes"] else "no "))
            print(f"{e['t']:6.1f}     {e['check']:<8} {mark} {e['conf']:.2f}  {e['reason'][:80]}")
        else:
            said = f"  ♪ \"{e['play']['text']}\"" if "play" in e else ""
            extra = f"  ({e['what']})" if e["kind"] == "warn" else ""
            print(f"{e['t']:6.1f}  >> {e['kind']:<9} step {e['step'] + 1} {e['title']}{extra}{said}")
    print(json.dumps(r["summary"]))


if __name__ == "__main__":
    main()
