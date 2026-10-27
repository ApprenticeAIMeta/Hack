"""Workpiece crops: SAM 3.1 tracks the workpiece through a run; each frame is cropped to it.

Glasses frames are mostly desk, laptop and hands; the workpiece is a small part of the image and
moves around as the head moves. Cropping to SAM's workpiece box makes the progress model look at
the state of the work instead of where the wearer happens to be looking.

The workpiece is what the hands are working on: of the tracks SAM finds for the workpiece
prompt, we keep the one that touches a tracked hand in the most frames. Picking by size alone
locks onto big static distractors (a notebook next to the sheet being folded).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from apprentice.models.meta import SAM, MetaClient

RECORD = re.compile(r"(?:^|,)(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>")
FRAME = re.compile(r"<(\d+)f>(.*)")


def parse_lines(text: str) -> dict[int, list[tuple]]:
    """SAM output lines -> {frame: [(id, x1, y1, x2, y2, conf)]}. Records follow the <Nf> marker."""
    frames: dict[int, list] = {}
    for line in text.splitlines():
        m = FRAME.match(line.strip())
        if m:
            frames[int(m.group(1))] = [(int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), float(g[7] or 1.0))
                                       for g in RECORD.findall(m.group(2))]
    return frames


def track(video: Path, prompt: str, client: MetaClient) -> dict:
    """SAM 3.1 over the whole clip. Returns {frame: [(id, x1, y1, x2, y2, conf)], "size": (w, h)}."""
    fid = client.upload(video)
    try:
        r = client.respond_stream(
            [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt}, {"type": "input_video", "file_id": fid}]}],
            model=SAM, metadata={"include_confidence": "true", "mask_encoding": "one_bit"})
    finally:
        client.delete_file(fid)
    frames: dict[int, list] = {}
    size = None
    for line in r.text.splitlines() or r.raw_lines:
        m = FRAME.match(line.strip())
        if not m:
            continue
        recs = []
        for g in RECORD.finditer(m.group(2)):
            oid, x1, y1, x2, y2, w, h, c = g.groups()
            size = (int(w), int(h))
            recs.append((int(oid), int(x1), int(y1), int(x2), int(y2), float(c or 1.0)))
        frames[int(m.group(1))] = recs
    return {"frames": frames, "size": size, "frames_billed": r.usage.get("video_frames_processed"),
            "latency_s": round(r.latency_s, 1)}


def _touches(a, b, margin: float = 0.05) -> bool:
    m = margin * max(a[2] - a[0], a[3] - a[1])
    return a[0] - m < b[2] and b[0] < a[2] + m and a[1] - m < b[3] and b[1] < a[3] + m


def workpiece_boxes(tr: dict, hands: dict, n_frames: int, min_conf: float = 0.5) -> list[tuple[int, int, int, int] | None]:
    """Per frame, the box of the workpiece track (or None when it is not visible)."""
    weight: dict[int, float] = defaultdict(float)
    for f, recs in tr["frames"].items():
        hb = [r[1:5] for r in hands["frames"].get(f, []) if r[5] >= min_conf]
        for oid, x1, y1, x2, y2, c in recs:
            if c >= min_conf and any(_touches((x1, y1, x2, y2), h) for h in hb):
                weight[oid] += c
    if not weight:
        return [None] * n_frames
    main = max(weight, key=weight.get)
    out = []
    for i in range(n_frames):
        recs = [r for r in tr["frames"].get(i, []) if r[5] >= min_conf]
        box = next((tuple(r[1:5]) for r in recs if r[0] == main), None)
        if box is None:  # track id lost: take the hand-touched record, if any
            hb = [r[1:5] for r in hands["frames"].get(i, []) if r[5] >= min_conf]
            touched = [r for r in recs if any(_touches(r[1:5], h) for h in hb)]
            if touched:
                box = tuple(max(touched, key=lambda r: r[5])[1:5])
        out.append(box)
    return out


def crop(frame: np.ndarray, box: tuple[int, int, int, int], size: int = 224, pad: float = 0.15) -> np.ndarray:
    """Square crop around the box with padding, clamped to the frame, resized to size x size."""
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = max(x2 - x1, y2 - y1) * (1 + 2 * pad)
    side = min(side, max(H, W))
    a, b = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    c, d = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
    patch = frame[b:d, a:c]
    ph, pw = patch.shape[:2]
    s = max(ph, pw)
    canvas = np.zeros((s, s, 3), frame.dtype)
    canvas[(s - ph) // 2:(s - ph) // 2 + ph, (s - pw) // 2:(s - pw) // 2 + pw] = patch
    return cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)


def read_frames(video: Path) -> tuple[list[np.ndarray], float]:
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 2.0
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    return frames, fps


def cached_track(video: Path, prompt: str, client: MetaClient, cache: Path) -> dict:
    f = cache / f"{video.stem}_sam_{prompt.replace(' ', '_')}.json"
    if f.exists():
        tr = json.loads(f.read_text())
        tr["frames"] = {int(k): v for k, v in tr["frames"].items()}
        return tr
    tr = track(video, prompt, client)
    f.write_text(json.dumps(tr))
    return tr


def crops_for_run(video: Path, prompt: str, client: MetaClient, cache: Path) -> dict:
    """Track workpiece and hands (cached), then crop every frame to the workpiece.
    Returns times, crops (None where not visible), boxes, and the raw frames."""
    tr = cached_track(video, prompt, client, cache)
    hands = cached_track(video, "hand", client, cache)
    frames, fps = read_frames(video)
    boxes = workpiece_boxes(tr, hands, len(frames))
    crops = [crop(f, b) if b else None for f, b in zip(frames, boxes)]
    return {"t": [i / fps for i in range(len(frames))], "crops": crops, "boxes": boxes, "frames": frames}
