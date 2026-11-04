"""Train a procedure's progress model from its expert runs.

    python -m apprentice.progress.train airplane \\
        expert1=../footage/plane_expert1_native.mp4 expert2=../footage/plane_expert2_native.mp4

Per run: 2 fps proxy -> SAM 3.1 workpiece + hand tracks (on the clean proxy) -> the same frames
after a simulated glasses stream (7 fps, 55 kbps HEVC) -> workpiece crops -> DINOv2. Both clean
and stream-degraded crops go in the training bank. The task span of each run is where the
workpiece is visible (or the compiled procedure's step range for the run it was compiled from).
Writes procedures/<name>/progress.npz.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from apprentice.models.meta import MetaClient
from apprentice.progress.crop import crop, crops_for_run, read_frames
from apprentice.progress.embed import Embedder
from apprentice.progress.model import Bank, ProgressModel

ROOT = Path(__file__).resolve().parents[3]
PROXY_W, FPS = 720, 2
STREAM_FPS, STREAM_KBPS = 15, 330   # the glasses' stream as measured on Nov 3


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def proxy(src: Path, dst: Path) -> Path:
    if not dst.exists():
        _ff("-i", str(src), "-an", "-vf", f"scale={PROXY_W}:-2,fps={FPS}", "-c:v", "libx264", "-crf", "23",
            "-preset", "veryfast", "-movflags", "+faststart", str(dst))
    return dst


def stream_sim(src: Path, dst: Path) -> Path:
    """What the same footage looks like after the glasses' stream (720p HEVC at about 330 kbps), resampled to 2 fps."""
    if not dst.exists():
        mid = dst.with_suffix(".s7.mp4")
        _ff("-i", str(src), "-an", "-vf", f"scale={PROXY_W}:-2,fps={STREAM_FPS}", "-c:v", "libx265",
            "-b:v", f"{STREAM_KBPS}k", "-x265-params", "log-level=error", "-preset", "fast", str(mid))
        _ff("-i", str(mid), "-vf", f"fps={FPS}", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", str(dst))
        mid.unlink()
    return dst


def run_bank(name: str, video: Path, workpiece: str, span: tuple[float, float] | None, cache: Path,
             client: MetaClient, emb: Embedder) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Embeddings, progress labels and times for one expert run (clean + stream-degraded)."""
    d = crops_for_run(proxy(video, cache / f"{name}_2fps.mp4"), workpiece, client, cache)
    t = np.array(d["t"])
    seen = [i for i, b in enumerate(d["boxes"]) if b is not None]
    a, b = span or (t[seen[0]], t[seen[-1]])
    keep = [i for i in seen if a <= t[i] <= b]
    degraded, _ = read_frames(stream_sim(video, cache / f"{name}_stream2.mp4"))
    keep_s = [i for i in keep if i < len(degraded)]
    X = np.vstack([emb([d["crops"][i] for i in keep]), emb([crop(degraded[i], d["boxes"][i]) for i in keep_s])])
    tt = np.concatenate([t[keep], t[keep_s]])
    return X, np.clip((tt - a) / (b - a), 0, 1), tt


def _covered(box: list, hands: list) -> float:
    """Fraction of the workpiece box under hand boxes (upper bound: overlaps are summed)."""
    x1, y1, x2, y2 = box
    area = max(1.0, (x2 - x1) * (y2 - y1))
    cov = 0.0
    for hx1, hy1, hx2, hy2 in hands:
        cov += max(0, min(x2, hx2) - max(x1, hx1)) * max(0, min(y2, hy2) - max(y1, hy1))
    return min(1.0, cov / area)


def pick_reference(d: dict, t_start: float, t_end: float, tail: float = 0.35) -> int | None:
    """The 2 fps frame that best shows the step's result: in the last part of the step, workpiece
    visible, hands off it as far as possible, then the sharpest. The compile's own pick prefers
    calm frames, which on a real recording are often hands resting on the work."""
    for lo in (t_end - tail * (t_end - t_start), t_start):
        cand = [i for i, t in enumerate(d["t"]) if lo <= t <= t_end and d["boxes"][i]]
        if not cand:
            continue
        cov = {i: _covered(d["boxes"][i], d["hands"][i]) for i in cand}
        best = min(cov.values())
        clear = [i for i in cand if cov[i] <= best + 0.05]
        return max(clear, key=lambda i: cv2.Laplacian(cv2.cvtColor(d["crops"][i], cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    return None


def reference_crops(pdir: Path, proc: dict, video: Path, d: dict, out_w: int = 512, pad: float = 0.3) -> None:
    """keyframes/step_XX.jpg and step_XX_crop.jpg: each step's reference frame, re-picked with the
    hands off the work, from the full-resolution source, cropped with the SAM box."""
    from apprentice.capture import media
    cap = cv2.VideoCapture(str(video))
    for s in proc["steps"]:
        i = pick_reference(d, s["t_start"], s["t_end"])
        if i is None:
            continue
        s["reference_frame"] = {"file": f"keyframes/{s['id']}.jpg", "t": round(d["t"][i], 2)}
        rf = s["reference_frame"]
        media.frame_at(video, rf["t"], pdir / rf["file"])
        box = d["boxes"][i]
        cap.set(cv2.CAP_PROP_POS_MSEC, rf["t"] * 1000)
        ok, f = cap.read()
        if not ok:
            continue
        H, W = f.shape[:2]
        sc = W / d["frames"][0].shape[1]
        x1, y1, x2, y2 = (v * sc for v in box)
        side = max(x2 - x1, y2 - y1) * (1 + 2 * pad)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        a, b = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        c, e = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
        crop = f[b:e, a:c]
        crop = cv2.resize(crop, (out_w, round(crop.shape[0] * out_w / crop.shape[1])), interpolation=cv2.INTER_AREA)
        name = f"keyframes/{s['id']}_crop.jpg"
        cv2.imwrite(str(pdir / name), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        s["reference_frame"]["crop"] = name
    cap.release()
    (pdir / "procedure.json").write_text(json.dumps(proc, indent=1))


def train(procedure: str, runs: dict[str, Path]) -> Path:
    pdir = ROOT / "procedures" / procedure
    proc = json.loads((pdir / "procedure.json").read_text())
    cache = ROOT / "out" / "progress"
    cache.mkdir(parents=True, exist_ok=True)
    client, emb = MetaClient(), Embedder()
    compiled_from = Path(proc["source"]["video"]).stem
    Xs, ps, rs, ts = [], [], [], []
    for r, (name, video) in enumerate(runs.items()):
        span = (proc["steps"][0]["t_start"], proc["steps"][-1]["t_end"]) if video.stem == compiled_from else None
        X, p, t = run_bank(name, video, proc["workpiece"], span, cache, client, emb)
        if span:
            reference_crops(pdir, proc, video, crops_for_run(cache / f"{name}_2fps.mp4", proc["workpiece"], client, cache))
        Xs.append(X), ps.append(p), rs.append(np.full(len(p), r)), ts.append(t)
        print(f"{name}: {len(p)} training frames")
    model = ProgressModel().fit(Bank(np.vstack(Xs), np.concatenate(ps), np.concatenate(rs), np.concatenate(ts)),
                                list(runs))
    out = pdir / "progress.npz"
    model.save(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Train a procedure's progress model")
    ap.add_argument("procedure")
    ap.add_argument("runs", nargs="+", help="name=path for each expert run")
    a = ap.parse_args()
    runs = {r.split("=", 1)[0]: Path(r.split("=", 1)[1]).resolve() for r in a.runs}
    print(f"-> {train(a.procedure, runs)}")


if __name__ == "__main__":
    main()
