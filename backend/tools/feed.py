"""Play a recorded clip into the server as if it were the phone, in real time.

    python tools/feed.py ../footage/plane_novice_7fps.mp4 [--url ws://localhost:8000/ws/device?procedure=airplane]

Sends each frame as JPEG at the clip's frame rate and prints the guidance the phone would play.
With --hevc, sends the clip's raw HEVC bitstream instead, exactly as the phone app forwards it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time

import cv2
import websockets


def hevc_access_units(path: str) -> list[bytes]:
    """Split the clip's Annex B HEVC stream into per-frame chunks (one VCL NAL per frame)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-c:v", "copy", "-bsf:v", "hevc_mp4toannexb",
                          "-f", "hevc", "pipe:1"], capture_output=True, check=True).stdout
    starts = [i for i in range(len(raw) - 4) if raw[i:i + 4] == b"\x00\x00\x00\x01"]
    units, cur = [], b""
    for a, b in zip(starts, starts[1:] + [len(raw)]):
        nal = raw[a:b]
        cur += nal
        if (nal[4] >> 1) & 0x3F < 32:   # VCL NAL ends an access unit for single-slice frames
            units.append(cur)
            cur = b""
    return units


async def feed(path: str, url: str, speed: float, hevc: bool = False) -> None:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 7.0
    async with websockets.connect(url, max_size=None) as ws:
        async def listen():
            async for m in ws:
                ev = json.loads(m)
                print(f"[{time.time() - t0:6.1f}s] ♪ {ev.get('kind')}: \"{ev.get('text')}\"  {ev.get('url')}")

        t0 = time.time()
        task = asyncio.create_task(listen())
        units = None
        if hevc:
            units = hevc_access_units(path)
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            await ws.send(json.dumps({"type": "config", "codec": "hevc", "width": w, "height": h}))
        i = 0
        while True:
            if units is not None:
                if i >= len(units):
                    break
                await ws.send(units[i])
            else:
                ok, f = cap.read()
                if not ok:
                    break
                await ws.send(cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes())
            i += 1
            delay = t0 + i / fps / speed - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
        await asyncio.sleep(5)  # let in-flight checks land
        task.cancel()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--url", default="ws://localhost:8000/ws/device?procedure=airplane")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--hevc", action="store_true", help="send raw HEVC like the phone app")
    a = ap.parse_args()
    asyncio.run(feed(a.video, a.url, a.speed, a.hevc))


if __name__ == "__main__":
    main()
