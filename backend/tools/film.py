"""Render a recorded session's guidance as a transparent overlay for video editing.

    python tools/film.py ../out/sessions/<dir> [--vertical] [--start 0 --end 30] [--font path.ttf]

Writes <dir>/overlay_<wide|vertical>.mov, ProRes 4444 with alpha at 4K, 30 fps, timed to the
session: drop it over the footage in Premiere. Everything drawn comes from ticks.jsonl.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from bisect import bisect_right
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
FPS = 30
INK, PANEL, BLUE, RED, YELLOW, GREEN = "#1C2226", "#F4F6F7", "#1E5AA8", "#C2312B", "#F5C400", "#2E7D4F"
CAPTION_S = {"sam": 0.8, "dino": 0.8, "spark": 1.6, "voice": 4.0}
WARN_S, DONE_S = 9.0, 1.5


def wrap(d: ImageDraw.ImageDraw, text: str, font, width: float) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if cur and d.textlength(f"{cur} {w}", font=font) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + [cur] if cur else lines


class Overlay:
    def __init__(self, session: Path, vertical: bool, font: str):
        self.ticks = [json.loads(l) for l in (session / "ticks.jsonl").read_text().splitlines()]
        self.times = [m["t"] for m in self.ticks]
        meta = json.loads((session / "meta.json").read_text())
        self.steps = json.loads((ROOT / "procedures" / meta["procedure"] / "procedure.json").read_text())["steps"]
        self.events = [e for m in self.ticks for e in m["events"]]
        self.curve = [(m["t"], m["progress"]) for m in self.ticks if m.get("progress") is not None]
        self.size = (2160, 3840) if vertical else (3840, 2160)
        self.vertical = vertical
        self.f = {s: ImageFont.truetype(font, s) for s in (40, 52, 64, 88, 140)}

    def since(self, t: float, kinds: set[str], within: float) -> list[dict]:
        return [e for e in self.events if e["kind"] in kinds and 0 <= t - e["t"] <= within]

    def frame(self, t: float) -> Image.Image:
        img = Image.new("RGBA", self.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        i = bisect_right(self.times, t) - 1
        if i < 0:
            return img
        now = self.ticks[i]
        W, H = self.size
        m = 80
        self.step_card(d, now, m, m, W - 2 * m if self.vertical else 1500)
        self.captions(d, t, now, m, H - m)
        self.chart(d, t, W - m - (W - 2 * m if self.vertical else 1500), H - m - (620 if not self.vertical else 800),
                   W - 2 * m if self.vertical else 1500, 500)
        self.banners(d, t, W, H, m)
        return img

    def step_card(self, d, now, x, y, w) -> None:
        s = self.steps[now["step"]]
        title = wrap(d, s["title"], self.f[88], w - 96)
        instr = wrap(d, s["instruction"], self.f[52], w - 96)
        h = 170 + 100 * len(title) + 66 * len(instr)
        d.rounded_rectangle((x, y, x + w, y + h), 32, fill=PANEL)
        n = len(self.steps)
        seg = (w - 96 - 12 * (n - 1)) / n
        for k in range(n):
            a = x + 48 + k * (seg + 12)
            d.rounded_rectangle((a, y + 40, a + seg, y + 56), 6,
                                fill=GREEN if k < now["step"] else BLUE if k == now["step"] else "#B4BBC0")
        yy = y + 76
        for line in title:
            d.text((x + 48, yy), line, font=self.f[88], fill=INK)
            yy += 100
        for line in instr:
            d.text((x + 48, yy), line, font=self.f[52], fill="#4A545B")
            yy += 66

    def captions(self, d, t, now, x, bottom) -> None:
        checks = self.since(t, {"check"}, CAPTION_S["spark"])
        ms = now.get("timing_ms") or {}
        items = []
        if now.get("box") and t - now["t"] <= CAPTION_S["sam"]:
            items += [f"SAM 3.1 finds the workpiece, {ms.get('sam', 0) / 1000:.1f} s", f"DINOv2 scores progress, {ms.get('model', 0)} ms"]
        if checks:
            items.append(f"Muse Spark checks the step, {checks[-1]['latency_s']:.1f} s")
        if [e for e in self.since(t, {"instruct", "warn"}, CAPTION_S["voice"]) if "play" in e]:
            items.append("Expert's voice, cut by Muse Voice Transcribe")
        y = bottom
        for text in reversed(items):
            y -= 84
            d.rounded_rectangle((x, y, x + d.textlength(text, font=self.f[40]) + 56, y + 68), 34, fill=(28, 34, 38, 215))
            d.text((x + 28, y + 12), text, font=self.f[40], fill="white")
            y -= 14

    def chart(self, d, t, x, y, w, h) -> None:
        d.rounded_rectangle((x, y, x + w, y + h), 32, fill=(244, 246, 247, 235))
        span = max(self.times[-1], 30.0)
        px = lambda q: x + 40 + (w - 80) * q / span
        py = lambda p: y + h - 40 - (h - 80) * p
        for e in self.events:
            if e["kind"] == "warn" and e["t"] <= t:
                d.line((px(e["t"]), y + 40, px(e["t"]), y + h - 40), fill=YELLOW, width=10)
        pts = [(px(a), py(b)) for a, b in self.curve if a <= t]
        if len(pts) > 1:
            d.line(pts, fill=BLUE, width=10, joint="curve")
        if pts:
            r = 16
            d.ellipse((pts[-1][0] - r, pts[-1][1] - r, pts[-1][0] + r, pts[-1][1] + r), fill=BLUE)

    def banners(self, d, t, W, H, m) -> None:
        warn = self.since(t, {"warn"}, WARN_S)
        done = self.since(t, {"step_done"}, DONE_S)
        if warn:
            e = warn[-1]
            lines = wrap(d, e.get("what") or "", self.f[64], W - 2 * m - 96)
            said = wrap(d, f"“{e['play']['text']}”", self.f[52], W - 2 * m - 96) if "play" in e else []
            h = 230 + 80 * len(lines) + 68 * len(said)
            top = (H - h) // 2
            d.rounded_rectangle((m, top, W - m, top + h), 32, fill=YELLOW)
            d.text((m + 48, top + 24), "Stop", font=self.f[140], fill=INK)
            yy = top + 190
            for line in lines:
                d.text((m + 48, yy), line, font=self.f[64], fill=INK)
                yy += 80
            for line in said:
                d.text((m + 48, yy), line, font=self.f[52], fill=INK)
                yy += 68
        elif done:
            text = f"Done: {self.steps[done[-1]['step']]['title']}"
            tw = d.textlength(text, font=self.f[64])
            d.rounded_rectangle(((W - tw) / 2 - 48, H / 2 - 70, (W + tw) / 2 + 48, H / 2 + 70), 32, fill=GREEN)
            d.text(((W - tw) / 2, H / 2 - 40), text, font=self.f[64], fill="white")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", type=Path)
    ap.add_argument("--vertical", action="store_true", help="9:16 for the X cut")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--font", default="/System/Library/Fonts/HelveticaNeue.ttc")
    a = ap.parse_args()
    o = Overlay(a.session, a.vertical, a.font)
    end = a.end if a.end is not None else o.times[-1] + WARN_S
    out = a.session / f"overlay_{'vertical' if a.vertical else 'wide'}.mov"
    W, H = o.size
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444",
                           "-pix_fmt", "yuva444p10le", str(out)], stdin=subprocess.PIPE)
    for n in range(int((end - a.start) * FPS)):
        ff.stdin.write(o.frame(a.start + n / FPS).tobytes())
    ff.stdin.close()
    ff.wait()
    print(out)


if __name__ == "__main__":
    main()
