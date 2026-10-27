"""ffmpeg helpers: audio for transcription, a small proxy video for Spark, clips and frames."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

LOUDNORM = "loudnorm=I=-18:TP=-2"


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
         "-of", "json", str(path)], check=True, capture_output=True, text=True).stdout
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    return {"duration": float(d["format"]["duration"]), "width": v.get("width"), "height": v.get("height"),
            "fps": float(num) / float(den or 1), "has_audio": any(s["codec_type"] == "audio" for s in d["streams"])}


def audio_level_db(path: Path) -> float:
    """Mean volume in dB; below about -30 dB narration is likely too quiet to transcribe."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    return float("-inf")


def extract_wav(src: Path, dst: Path, rate: int = 16000) -> Path:
    """Mono 16-bit PCM WAV, loudness-normalized: the format Muse Voice Transcribe accepts."""
    _ff("-i", str(src), "-vn", "-af", LOUDNORM, "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst))
    return dst


def proxy_video(src: Path, dst: Path, width: int = 540, fps: int = 10) -> Path:
    """Small H.264 copy with normalized audio for Spark video input (about 45 KB/s)."""
    _ff("-i", str(src), "-vf", f"scale={width}:-2,fps={fps}", "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
        "-af", LOUDNORM, "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(dst))
    return dst


def cut_audio(src: Path, dst: Path, start: float, end: float, pad: float = 0.25) -> Path:
    """AAC clip of [start, end] with a little padding and short fades, for playback on the glasses."""
    s = max(0.0, start - pad)
    d = end + pad - s
    fade = min(0.08, d / 4)
    _ff("-ss", f"{s:.3f}", "-t", f"{d:.3f}", "-i", str(src), "-vn",
        "-af", f"{LOUDNORM},afade=t=in:d={fade},afade=t=out:st={d - fade:.3f}:d={fade}",
        "-ac", "1", "-ar", "24000", "-c:a", "aac", "-b:a", "64k", str(dst))
    return dst


def frame_at(src: Path, t: float, dst: Path, width: int = 768) -> Path:
    _ff("-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(dst))
    return dst
