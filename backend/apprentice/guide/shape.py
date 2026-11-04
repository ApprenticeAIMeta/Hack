"""The workpiece's silhouette, from SAM 3.1's mask, and checks that are geometry rather than opinion.

A vision-language model is weak at "does this edge meet that crease" on white paper with hands
and a busy table in frame. SAM already returns a pixel mask of the paper, so the paper can be cut
out onto a plain background before Spark looks at it, and some mistakes can be measured on the
mask directly: a wing or corner done on one side only makes the outline lopsided about its long
axis, and a result whose outline does not match the expert's after alignment differs in shape.
"""

from __future__ import annotations

import base64

import cv2
import numpy as np
from meta_sam_parser import CompletedOutcome, SegmentationMaskRecord, decode_mask_to_raster, image_segmentation_format

from apprentice.models.meta import SAM, MetaClient


def masks(client: MetaClient, img: np.ndarray, prompt: str, timeout=None) -> list[tuple[float, np.ndarray]]:
    """(confidence, full-image boolean mask) for each match of `prompt` in `img`."""
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    kw = {"timeout": timeout} if timeout is not None else {}
    r = client.respond_stream([{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": prompt},
        {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(buf).decode()}]}],
        model=SAM, metadata={"include_confidence": "true", "mask_encoding": "one_bit"}, **kw)
    parser = image_segmentation_format().create_parser()
    parser.push(r.text)
    result = parser.finish(CompletedOutcome()).result
    H, W = img.shape[:2]
    out = []
    for rec in result.records:
        if not isinstance(rec, SegmentationMaskRecord):
            continue
        m, b = rec.mask, rec.bounds   # one byte per pixel, m.width x m.height, placed at (left, top)
        grid = np.frombuffer(decode_mask_to_raster(m), np.uint8)[: m.width * m.height].reshape(m.height, m.width) > 0
        full = np.zeros((H, W), bool)
        h, w = min(m.height, H - b.top), min(m.width, W - b.left)
        full[b.top:b.top + h, b.left:b.left + w] = grid[:h, :w]
        out.append((float(rec.confidence if rec.confidence is not None else 1.0), full))
    return out


def cutout(img: np.ndarray, mask: np.ndarray, hands: np.ndarray | None = None, pad: float = 0.15,
           out_w: int = 640) -> np.ndarray:
    """The paper alone on mid-grey, cropped to its extent; hand pixels are greyed out too."""
    keep = mask & ~hands if hands is not None else mask
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return img
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    px, py = int((x2 - x1) * pad), int((y2 - y1) * pad)
    H, W = mask.shape
    x1, y1, x2, y2 = max(0, x1 - px), max(0, y1 - py), min(W, x2 + px), min(H, y2 + py)
    canvas = np.full_like(img, 128)
    canvas[keep] = img[keep]
    c = canvas[y1:y2, x1:x2]
    return cv2.resize(c, (out_w, round(c.shape[0] * out_w / c.shape[1])), interpolation=cv2.INTER_AREA)


def _upright(mask: np.ndarray, size: int = 256) -> np.ndarray:
    """The mask rotated so its long axis is vertical, scaled into a size x size square."""
    ys, xs = np.nonzero(mask)
    pts = np.c_[xs, ys].astype(np.float32)
    (cx, cy), (w, h), angle = cv2.minAreaRect(pts)
    if w > h:
        angle += 90
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    H, W = mask.shape
    rot = cv2.warpAffine(mask.astype(np.uint8) * 255, M, (W, H), flags=cv2.INTER_NEAREST)
    ys, xs = np.nonzero(rot)
    if len(xs) == 0:
        return np.zeros((size, size), bool)
    c = rot[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    s = size / max(c.shape)
    c = cv2.resize(c, (max(1, round(c.shape[1] * s)), max(1, round(c.shape[0] * s))), interpolation=cv2.INTER_NEAREST)
    out = np.zeros((size, size), np.uint8)
    oy, ox = (size - c.shape[0]) // 2, (size - c.shape[1]) // 2
    out[oy:oy + c.shape[0], ox:ox + c.shape[1]] = c
    return out > 0


def symmetry(mask: np.ndarray) -> float:
    """IoU of the upright silhouette with its mirror image about the long axis: 1.0 is perfectly
    symmetric; a wing or corner folded on one side only drops it."""
    u = _upright(mask)
    m = u[:, ::-1]
    return float((u & m).sum() / max(1, (u | m).sum()))


def match(mask: np.ndarray, ref: np.ndarray) -> float:
    """IoU of two upright silhouettes (the second also tried flipped, since the paper may face
    either way)."""
    a, b = _upright(mask), _upright(ref)
    best = 0.0
    for bb in (b, b[::-1], b[:, ::-1], b[::-1, ::-1]):
        best = max(best, float((a & bb).sum() / max(1, (a | bb).sum())))
    return best
