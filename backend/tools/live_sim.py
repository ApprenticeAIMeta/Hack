"""Run a live Session in-process on a recording (raw HEVC at real time) and log every tick.

    python tools/live_sim.py ../footage/plane_novice_7fps.mp4 --seconds 30

Same code path as the phone (Session.push_hevc), without the server; writes
out/live_sim/<clip>.jsonl with time, box, progress, step and events per tick.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cv2  # noqa: E402

from apprentice.guide.session import ROOT, Session  # noqa: E402
from apprentice.progress.embed import Embedder  # noqa: E402
from feed import hevc_access_units  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--procedure", default="airplane")
    ap.add_argument("--seconds", type=float, default=None)
    a = ap.parse_args()

    out = ROOT / "out" / "live_sim" / f"{Path(a.video).stem}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    log = open(out, "w")

    def publish(m: dict) -> None:
        if m["type"] == "state":
            m = {k: v for k, v in m.items() if k != "jpeg"}
            log.write(json.dumps(m) + "\n")
            log.flush()
            ev = [f"{e['kind']}:{e['step'] + 1}" for e in m["events"] if e["kind"] != "check"]
            p = "-" if m["progress"] is None else f"{m['progress']:.2f}"
            print(f"t={m['t']:5.1f} p={p} step={m['step'] + 1} box={m['box']} {' '.join(ev)}")
        elif m["type"] == "error":
            print("ERROR", m["message"])

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 7.0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    s = Session(a.procedure, publish, Embedder())
    s.configure_hevc(w, h)
    units = hevc_access_units(a.video)
    t0 = time.time()
    for i, u in enumerate(units):
        if a.seconds and i / fps > a.seconds:
            break
        s.push_hevc(u)
        delay = t0 + (i + 1) / fps - time.time()
        if delay > 0:
            time.sleep(delay)
    time.sleep(4)
    s.close()
    print(f"-> {out}")


if __name__ == "__main__":
    main()
