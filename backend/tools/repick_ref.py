"""Re-pick a step's reference frame by hand: the frame at T, cropped to the workpiece box.

    PYTHONPATH=. .venv/bin/python tools/repick_ref.py thermocouple step_03 150.0 [--box x1 y1 x2 y2]

train.py's automatic pick minimizes hand coverage and picks the sharpest frame, but when the
expert's hands never leave the work (thermocouple step_03/04) it can land on an occluded frame,
or the same frame for two steps. This overrides one step. --box skips the SAM call (pixel coords
in the full-resolution source frame).
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import cv2

from apprentice.capture import media
from apprentice.models.meta import SAM, MetaClient
from apprentice.progress.crop import parse_lines

ROOT = Path(__file__).resolve().parents[2]


def sam_box(img, prompt: str, client: MetaClient):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    url = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
    r = client.respond_stream([{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": prompt}, {"type": "input_image", "image_url": url}]}],
        model=SAM, metadata={"include_confidence": "true", "mask_encoding": "one_bit"}, timeout=60)
    got = [r_[1:] for r_ in parse_lines(r.text).get(0, []) if r_[5] >= 0.5]
    return tuple(max(got, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))[:4]) if got else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-pick one step's reference frame")
    ap.add_argument("procedure")
    ap.add_argument("step", help="e.g. step_03")
    ap.add_argument("t", type=float, help="time in the source video, seconds")
    ap.add_argument("--box", nargs=4, type=float, default=None,
                    metavar=("X1", "Y1", "X2", "Y2"))
    ap.add_argument("--prompt", default=None, help="SAM text prompt for the box (default: the procedure's workpiece)")
    ap.add_argument("--frame", type=Path, default=None,
                    help="read this exact image instead of seeking the video (what you see is what you get)")
    a = ap.parse_args()

    pdir = ROOT / "procedures" / a.procedure
    proc = json.loads((pdir / "procedure.json").read_text())
    step = next(s for s in proc["steps"] if s["id"] == a.step)
    video = ROOT / "footage" / proc["source"]["video"]

    if a.frame:
        f = cv2.imread(str(a.frame))
        if f is None:
            raise SystemExit(f"cannot read {a.frame}")
        cv2.imwrite(str(pdir / "keyframes" / f"{a.step}.jpg"), f, [cv2.IMWRITE_JPEG_QUALITY, 90])
    else:
        media.frame_at(video, a.t, pdir / "keyframes" / f"{a.step}.jpg")
        cap = cv2.VideoCapture(str(video))
        cap.set(cv2.CAP_PROP_POS_MSEC, a.t * 1000)
        ok, f = cap.read()
        cap.release()
        if not ok:
            raise SystemExit(f"no frame at t={a.t}")

    prompt = a.prompt or proc["workpiece"]
    box = tuple(a.box) if a.box else sam_box(f, prompt, MetaClient())
    if box is None:
        raise SystemExit(f"SAM found no '{prompt}' at t={a.t}; pass --box")

    H, W = f.shape[:2]
    x1, y1, x2, y2 = box
    side = max(x2 - x1, y2 - y1) * 1.6   # pad 0.3, as in reference_crops
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    c1, r1 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    c2, r2 = int(min(W, cx + side / 2)), int(min(H, cy + side / 2))
    crop = f[r1:r2, c1:c2]
    crop = cv2.resize(crop, (512, round(crop.shape[0] * 512 / crop.shape[1])), interpolation=cv2.INTER_AREA)
    out = f"keyframes/{a.step}_crop.jpg"
    cv2.imwrite(str(pdir / out), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])

    step["reference_frame"] = {"file": f"keyframes/{a.step}.jpg", "t": round(a.t, 2), "crop": out}
    (pdir / "procedure.json").write_text(json.dumps(proc, indent=1))
    print(f"{a.step}: ref t={a.t} box={tuple(int(v) for v in box)} -> {out}")


if __name__ == "__main__":
    main()
