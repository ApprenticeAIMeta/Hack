"""Does a sharper picture help the locate check? Same moments, same crop, two image qualities.

    python eval/bench_stills.py [--frames 24] [--passes 2]     (from backend/)

Uses the expert1 run, which the references do not come from: full-quality frames scaled to the width
of a stream still (1080) against the same run through the simulated stream. Labels were read from a
5 s contact sheet, so frames stay MARGIN_S from every step change. Writes docs/stills-test.md.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apprentice.guide.policy import SparkPolicy  # noqa: E402
from apprentice.models.meta import SAM, MetaClient  # noqa: E402
from apprentice.progress.crop import _touches, parse_lines  # noqa: E402
from apprentice.replay import ROOT  # noqa: E402

STEP_DONE = [36, 55, 72, 92, 102, 119]
MARGIN_S = 4.0
STILL_W = 1080
NATIVE = ROOT / "footage" / "plane_expert1_native.mp4"
STREAM = ROOT / "out" / "progress" / "expert1_stream7.mp4"


def retry(fn, tries: int = 4):
    """Network hiccups must not end a run; a failed check stays failed after the last try."""
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def sharpest(path: Path, t: float, span: float = 0.5) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int((t - span) * fps))
    best, top = None, -1.0
    for _ in range(max(1, int(2 * span * fps))):
        ok, f = cap.read()
        if not ok:
            break
        s = cv2.Laplacian(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        if s > top:
            best, top = f, s
    cap.release()
    return best


def find(client: MetaClient, img: np.ndarray, phrase: str) -> list[tuple]:
    url = "data:image/jpeg;base64," + __import__("base64").b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()
    r = retry(lambda: client.respond_stream([{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": phrase}, {"type": "input_image", "image_url": url}]}],
        model=SAM, metadata={"include_confidence": "true", "mask_encoding": "one_bit"}))
    return [x[1:] for x in parse_lines(r.text).get(0, []) if x[5] >= 0.5]


def cut(img: np.ndarray, box: tuple, pad: float = 0.3, out_w: int = 640) -> np.ndarray:
    """box in fractions of the image, so both qualities crop the same region."""
    H, W = img.shape[:2]
    x1, y1, x2, y2 = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    side = max(x2 - x1, y2 - y1) * (1 + 2 * pad)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    v = img[int(max(0, cy - side / 2)):int(min(H, cy + side / 2)), int(max(0, cx - side / 2)):int(min(W, cx + side / 2))]
    return cv2.resize(v, (out_w, round(v.shape[0] * out_w / v.shape[1])), interpolation=cv2.INTER_CUBIC)


def moment(client: MetaClient, t: float) -> dict | None:
    native = sharpest(NATIVE, t)
    still = cv2.resize(native, (STILL_W, round(native.shape[0] * STILL_W / native.shape[1])), interpolation=cv2.INTER_AREA)
    wp, hands = find(client, still, "paper"), find(client, still, "hand")
    pick = [r for r in wp if any(_touches(r[:4], h[:4]) for h in hands)] or wp
    if not pick:
        return None
    H, W = still.shape[:2]
    b = max(pick, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]) * r[4])[:4]
    box = (b[0] / W, b[1] / H, b[2] / W, b[3] / H)
    return {"t": t, "truth": sum(d <= t for d in STEP_DONE), "still": cut(still, box), "stream": cut(sharpest(STREAM, t), box)}


def score(rows: list[dict]) -> dict:
    use = [r for r in rows if not r["unclear"]]
    conf = [r for r in use if r["conf"] >= 0.85]
    by = {(r["t"], r["p"]): r["n"] for r in rows}
    same = [by[(t, 0)] == by[(t, 1)] for t, p in by if p == 0 and (t, 1) in by]
    return {"answers": len(rows), "exact": round(float(np.mean([r["n"] == r["truth"] for r in use])), 2),
            "within1": round(float(np.mean([abs(r["n"] - r["truth"]) <= 1 for r in use])), 2),
            "mean_err": round(float(np.mean([abs(r["n"] - r["truth"]) for r in use])), 2),
            "unclear": len(rows) - len(use), "conf_n": len(conf),
            "conf_exact": round(float(np.mean([r["n"] == r["truth"] for r in conf])), 2) if conf else None,
            "repeat": round(float(np.mean(same)), 2) if same else None,
            "lat_med": round(st.median(r["lat"] for r in rows), 2)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--passes", type=int, default=2)
    a = ap.parse_args()
    client = MetaClient()
    pol = SparkPolicy(ROOT / "procedures" / "airplane", client)
    ok = [t / 2 for t in range(2, 240) if all(abs(t / 2 - d) >= MARGIN_S for d in STEP_DONE)]
    times = [ok[int(j)] for j in np.linspace(0, len(ok) - 1, a.frames)]
    with ThreadPoolExecutor(4) as ex:
        moments = [m for m in ex.map(lambda t: moment(client, t), times) if m]
    print("moments", len(moments), "truth", np.bincount([m["truth"] for m in moments]).tolist(), flush=True)
    out = ROOT / "out" / "quality"
    pairs = [np.hstack([m[k][:360] if m[k].shape[0] >= 360 else cv2.copyMakeBorder(m[k], 0, 360 - m[k].shape[0], 0, 0, cv2.BORDER_CONSTANT)
                        for k in ("still", "stream")]) for m in moments[::6]]
    cv2.imwrite(str(out / "stills_vs_stream.jpg"), np.vstack(pairs))

    def ask(job):
        m, kind, p = job
        c = retry(lambda: pol.locate(m[kind]))
        return {"kind": kind, "t": m["t"], "p": p, "truth": m["truth"], "n": c.completed, "conf": c.confidence,
                "unclear": c.unclear, "lat": c.latency_s}
    jobs = [(m, k, p) for p in range(a.passes) for m in moments for k in ("still", "stream")]
    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(ask, jobs))
    (ROOT / "out" / "bench_stills.json").write_text(json.dumps(rows, indent=1))
    res = {k: score([r for r in rows if r["kind"] == k]) for k in ("still", "stream")}
    print(json.dumps(res, indent=1))
    names = {"still": f"Full quality at {STILL_W} wide", "stream": "Simulated stream"}
    md = ("# Do sharper stills help the locate check?\n\nGenerated by `backend/eval/bench_stills.py`: Muse Spark 1.3 on "
          f"{len(moments)} moments of one expert run, {a.passes} passes, same crop for both qualities. Full-quality recording frames "
          "stand in for real stills, which have not been tested yet. An expert run is cleaner than a novice's, and labels are good to about 5 s.\n\n"
          "| Image | Exactly right | Within one step | Mean steps off | Unclear | Exact at confidence >= 0.85 | Same on repeat | Latency median |\n|---|---|---|---|---|---|---|---|\n")
    for k, x in res.items():
        md += f"| {names[k]} | {x['exact']} | {x['within1']} | {x['mean_err']} | {x['unclear']} | {x['conf_exact']} (n={x['conf_n']}) | {x['repeat']} | {x['lat_med']} s |\n"
    (ROOT / "docs" / "stills-test.md").write_text(md)


if __name__ == "__main__":
    main()
