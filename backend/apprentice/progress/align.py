"""Score a run with a procedure's progress model and render the live mirror offline.

    python -m apprentice.progress.align airplane ../footage/plane_novice_7fps.mp4 --name novice --task-end 100

Writes out/align/<name>/: progress.csv, curve.png, mirror.mp4 (run on the left, the expert at the
same progress on the right, both progress curves underneath). With --task-end, also reports the
Spearman rank correlation between predicted progress and elapsed time over the task.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.stats import spearmanr

from apprentice.models.meta import MetaClient
from apprentice.progress.crop import crops_for_run, read_frames
from apprentice.progress.embed import Embedder
from apprentice.progress.model import ProgressModel, causal_median
from apprentice.progress.train import proxy

ROOT = Path(__file__).resolve().parents[3]
W, H = 360, 480           # each panel
CURVE_H = 160
NOVICE, EXPERT = (80, 170, 255), (120, 220, 120)  # BGR


def score(video: Path, name: str, model: ProgressModel, workpiece: str) -> dict:
    cache = ROOT / "out" / "progress"
    d = crops_for_run(proxy(video, cache / f"{name}_2fps.mp4"), workpiece, MetaClient(), cache)
    keep = [i for i, c in enumerate(d["crops"]) if c is not None]
    pred = model.predict(Embedder()([d["crops"][i] for i in keep]))
    prog = np.full(len(d["crops"]), np.nan)
    prog[keep] = causal_median(pred["progress"])
    raw = np.full(len(d["crops"]), np.nan)
    raw[keep] = pred["progress"]
    sim = np.full(len(d["crops"]), np.nan)
    sim[keep] = pred["similarity"]
    return {"t": np.array(d["t"]), "progress": prog, "raw": raw, "sim": sim, "frames": d["frames"],
            "boxes": d["boxes"], "crops": d["crops"]}


def step_at(proc: dict, t_expert: float) -> str:
    for s in proc["steps"]:
        if s["t_start"] <= t_expert <= s["t_end"]:
            return s["title"]
    return ""


def _fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    s = min(w / iw, h / ih)
    r = cv2.resize(img, (int(iw * s), int(ih * s)))
    out = np.zeros((h, w, 3), np.uint8)
    y, x = (h - r.shape[0]) // 2, (w - r.shape[1]) // 2
    out[y:y + r.shape[0], x:x + r.shape[1]] = r
    return out


def _curve(t: np.ndarray, p: np.ndarray, upto: int, width: int) -> np.ndarray:
    img = np.full((CURVE_H, width, 3), 24, np.uint8)
    T = t[-1] or 1
    pts = lambda tt, pp: (int(10 + (width - 20) * tt / T), int(CURVE_H - 12 - (CURVE_H - 24) * pp))
    cv2.line(img, pts(0, 0), pts(T, 1), EXPERT, 1, cv2.LINE_AA)  # expert reference: steady progress
    prev = None
    for i in range(upto + 1):
        if np.isnan(p[i]):
            prev = None
            continue
        q = pts(t[i], p[i])
        if prev:
            cv2.line(img, prev, q, NOVICE, 2, cv2.LINE_AA)
        prev = q
    if not np.isnan(p[upto]):
        cv2.circle(img, pts(t[upto], p[upto]), 4, (255, 255, 255), -1)
    cv2.putText(img, "progress", (10, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
    return img


def render(s: dict, model: ProgressModel, proc: dict, expert_run: str, expert_video: Path, out: Path) -> None:
    ex_frames, ex_fps = read_frames(expert_video)
    vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), 2, (2 * W, H + CURVE_H))
    last_ex = np.zeros((H, W, 3), np.uint8)
    for i, f in enumerate(s["frames"]):
        left = f.copy()
        if s["boxes"][i]:
            x1, y1, x2, y2 = s["boxes"][i]
            cv2.rectangle(left, (x1, y1), (x2, y2), NOVICE, 2)
        left = _fit(left, W, H)
        label = ""
        if not np.isnan(s["progress"][i]):
            te = model.mirror(float(s["progress"][i]), expert_run)
            last_ex = _fit(ex_frames[min(int(te * ex_fps), len(ex_frames) - 1)], W, H)
            label = step_at(proc, te)
            cv2.putText(left, f"{100 * s['progress'][i]:.0f}%", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, NOVICE, 2)
        right = last_ex.copy()
        cv2.putText(left, f"novice {s['t'][i]:.0f}s", (10, H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(right, f"expert: {label}", (10, H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        vw.write(np.vstack([np.hstack([left, right]), _curve(s["t"], s["progress"], i, 2 * W)]))
    vw.release()


def main() -> None:
    ap = argparse.ArgumentParser(description="Score a run and render the mirror")
    ap.add_argument("procedure")
    ap.add_argument("video", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--expert", default=None, help="expert run name shown in the mirror (default: last trained run)")
    ap.add_argument("--task-end", type=float, default=None, help="seconds; enables rank-correlation report")
    a = ap.parse_args()

    pdir = ROOT / "procedures" / a.procedure
    proc = json.loads((pdir / "procedure.json").read_text())
    model = ProgressModel.load(pdir / "progress.npz")
    expert = a.expert or model.runs[-1]
    s = score(a.video.resolve(), a.name, model, proc["workpiece"])

    out = ROOT / "out" / "align" / a.name
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "progress.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "progress", "raw"])
        for t, p, r in zip(s["t"], s["progress"], s["raw"]):
            w.writerow([f"{t:.2f}", "" if np.isnan(p) else f"{p:.4f}", "" if np.isnan(r) else f"{r:.4f}"])
    if a.task_end:
        m = ~np.isnan(s["progress"]) & (s["t"] <= a.task_end)
        print(f"rank correlation over task: {spearmanr(s['progress'][m], s['t'][m])[0]:.3f} "
              f"(raw {spearmanr(s['raw'][m], s['t'][m])[0]:.3f}), {m.sum()} frames")
    render(s, model, proc, expert, ROOT / "out" / "progress" / f"{expert}_2fps.mp4", out / "mirror.mp4")
    last = len(s["t"]) - 1
    cv2.imwrite(str(out / "curve.png"), _curve(s["t"], s["progress"], last, 2 * W))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
