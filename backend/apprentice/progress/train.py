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

import numpy as np

from apprentice.models.meta import MetaClient
from apprentice.progress.crop import crop, crops_for_run, read_frames
from apprentice.progress.embed import Embedder
from apprentice.progress.model import Bank, ProgressModel

ROOT = Path(__file__).resolve().parents[3]
PROXY_W, FPS = 504, 2
STREAM_FPS, STREAM_KBPS = 7, 55


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def proxy(src: Path, dst: Path) -> Path:
    if not dst.exists():
        _ff("-i", str(src), "-an", "-vf", f"scale={PROXY_W}:-2,fps={FPS}", "-c:v", "libx264", "-crf", "23",
            "-preset", "veryfast", "-movflags", "+faststart", str(dst))
    return dst


def stream_sim(src: Path, dst: Path) -> Path:
    """What the same footage looks like after the glasses' Bluetooth stream, resampled to 2 fps."""
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
