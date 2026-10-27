"""Frame gate: keep frames that are sharp and taken while the head is still.

Glasses footage is mostly motion blur from head movement. Per frame we score
  sharpness = variance of the Laplacian (higher is sharper)
  motion    = median optical-flow magnitude between consecutive frames, on a small
              grayscale copy. The median tracks camera (head) motion; moving hands
              cover a minority of pixels, so working frames are not rejected.

Offline (`select_keyframes`): the sharpest frame per window among frames whose motion is at
or below a percentile of the clip. Live (`LiveGate`): same scores, decided frame by frame
against rolling statistics, emitting at most `max_fps`.
"""

from __future__ import annotations

import argparse
import csv
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

FLOW_SIZE = (126, 224)  # (w, h) for a portrait 9:16 frame; flow is cheap at this size


@dataclass
class FrameScore:
    index: int
    t: float
    sharpness: float
    motion: float


class Scorer:
    """Stateful per-frame scorer; feed frames in order."""

    def __init__(self):
        self._prev: np.ndarray | None = None

    def __call__(self, bgr: np.ndarray) -> tuple[float, float]:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        h, w = gray.shape
        size = FLOW_SIZE if h >= w else FLOW_SIZE[::-1]
        small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        if self._prev is None:
            motion = 0.0
        else:
            flow = cv2.calcOpticalFlowFarneback(self._prev, small, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            motion = float(np.median(np.linalg.norm(flow, axis=2)))
        self._prev = small
        return sharp, motion


def score_video(path: Path) -> tuple[list[FrameScore], float]:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    scorer = Scorer()
    scores = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        s, m = scorer(frame)
        scores.append(FrameScore(i, i / fps, s, m))
        i += 1
    cap.release()
    return scores, fps


def select_keyframes(scores: list[FrameScore], window_s: float = 1.0, motion_pct: float = 60.0) -> list[FrameScore]:
    if not scores:
        return []
    limit = float(np.percentile([s.motion for s in scores[1:]] or [0.0], motion_pct))
    picked = []
    end = scores[-1].t
    t = 0.0
    while t <= end:
        win = [s for s in scores if t <= s.t < t + window_s and s.motion <= limit]
        if win:
            picked.append(max(win, key=lambda s: s.sharpness))
        t += window_s
    return picked


class LiveGate:
    """Online gate for the live stream: pass a frame unless it is among the blurriest or shakiest
    of the recent history (percentiles), at most `max_fps` frames per second. `offer` also never
    goes quiet: after `max_wait_s` without a pass it releases the sharpest frame it saw meanwhile."""

    def __init__(self, max_fps: float = 2.0, history: int = 30, sharp_pct: float = 40.0, motion_pct: float = 60.0,
                 max_wait_s: float = 1.5):
        self.min_dt = 1.0 / max_fps
        self.max_wait = max_wait_s
        self._best: tuple[float, np.ndarray] | None = None
        self.sharp_pct, self.motion_pct = sharp_pct, motion_pct
        self._scorer = Scorer()
        self._sharp = deque(maxlen=history)
        self._motion = deque(maxlen=history)
        self._last_t = -1e9

    def __call__(self, bgr: np.ndarray, t: float) -> bool:
        s, m = self._scorer(bgr)
        warm = len(self._sharp) >= 5
        ok = (not warm) or (s >= np.percentile(self._sharp, self.sharp_pct)
                                and m <= np.percentile(self._motion, self.motion_pct))
        self._sharp.append(s)
        self._motion.append(m)
        self._score = s
        if ok and t - self._last_t >= self.min_dt:
            self._last_t = t
            return True
        return False

    def offer(self, bgr: np.ndarray, t: float) -> np.ndarray | None:
        """The frame to process now, if any: this one if it passes, else the sharpest held frame
        once the gate has been quiet for max_wait_s."""
        if self(bgr, t):
            self._best = None
            return bgr
        if self._best is None or self._score > self._best[0]:
            self._best = (self._score, bgr)
        if t - self._last_t >= self.max_wait:
            self._last_t = t
            frame, self._best = self._best[1], None
            return frame
        return None


def write_frames(path: Path, picked: list[FrameScore], out_dir: Path, width: int = 768) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(path))
    files = []
    for s in picked:
        cap.set(cv2.CAP_PROP_POS_FRAMES, s.index)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
        f = out_dir / f"kf_{s.t:07.2f}.jpg"
        cv2.imwrite(str(f), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        files.append(f)
    cap.release()
    return files


def contact_sheet(files: list[Path], out: Path, cols: int = 8, thumb_w: int = 160) -> None:
    thumbs = []
    for f in files:
        im = cv2.imread(str(f))
        h, w = im.shape[:2]
        im = cv2.resize(im, (thumb_w, round(h * thumb_w / w)))
        cv2.putText(im, f.stem.removeprefix("kf_"), (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        thumbs.append(im)
    if not thumbs:
        return
    th = max(t.shape[0] for t in thumbs)
    thumbs = [cv2.copyMakeBorder(t, 0, th - t.shape[0], 0, 0, cv2.BORDER_CONSTANT) for t in thumbs]
    while len(thumbs) % cols:
        thumbs.append(np.zeros_like(thumbs[0]))
    rows = [np.hstack(thumbs[i:i + cols]) for i in range(0, len(thumbs), cols)]
    cv2.imwrite(str(out), np.vstack(rows))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--motion-pct", type=float, default=60.0)
    a = ap.parse_args()

    scores, fps = score_video(a.video)
    picked = select_keyframes(scores, a.window, a.motion_pct)
    a.out.mkdir(parents=True, exist_ok=True)
    with open(a.out / "scores.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "t", "sharpness", "motion", "kept"])
        kept = {s.index for s in picked}
        for s in scores:
            w.writerow([s.index, f"{s.t:.3f}", f"{s.sharpness:.1f}", f"{s.motion:.3f}", int(s.index in kept)])
    files = write_frames(a.video, picked, a.out / "keyframes")
    contact_sheet(files, a.out / "contact.jpg")
    print(f"{len(scores)} frames @ {fps:.1f} fps -> {len(files)} keyframes in {a.out}")


if __name__ == "__main__":
    main()
