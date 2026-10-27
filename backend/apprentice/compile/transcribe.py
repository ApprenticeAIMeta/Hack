"""Expert narration -> timed utterances, via Muse Voice Transcribe.

Transcribe's own turns are often 20-30 s long, too coarse to cut guidance clips from, and
Spark's audio timestamps drift. So we find utterances ourselves from an energy envelope
relative to the room's noise floor (fixed silence thresholds fail in noisy rooms), cap them
at a few seconds, and transcribe each one separately. Every Turn's times are then exact
cut points for the expert's voice.
"""

from __future__ import annotations

import re
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from apprentice.models.meta import MetaClient

SPECIAL = re.compile(r"<\|[^|>]*\|>")
# ASR stutter at segment edges: "halves. halves.", "I'm I'm", "of of it"
REPEAT = re.compile(r"\b(\w[\w']*(?:\s+\w[\w']*){0,3})([.,!?]*)(\s+\1\b[.,!?]*)+", re.IGNORECASE)


@dataclass
class Turn:
    id: int
    start: float  # seconds
    end: float
    text: str


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", SPECIAL.sub(" ", text)).strip()
    text = REPEAT.sub(lambda m: m.group(1) + m.group(2), text)
    return re.sub(r"([.,!?])[.,!?]+", r"\1", text)


HOP_S = 0.05


def utterances(wav: Path, margin_db: float = 6.0, join_gap: float = 1.2, max_len: float = 8.0,
               min_len: float = 0.4) -> list[tuple[float, float]]:
    """Speech spans in seconds: frames louder than the noise floor + margin, joined across short
    gaps, split at the quietest frame when longer than max_len."""
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    hop = int(HOP_S * sr)
    n = len(x) // hop
    db = 20 * np.log10(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    speech = db > np.percentile(db, 20) + margin_db

    spans: list[list[int]] = []
    i = 0
    while i < n:
        if speech[i]:
            j = i
            while j < n and speech[j]:
                j += 1
            if spans and (i - spans[-1][1]) * HOP_S < join_gap:
                spans[-1][1] = j
            else:
                spans.append([i, j])
            i = j
        else:
            i += 1

    out: list[tuple[int, int]] = []
    limit = int(max_len / HOP_S)
    stack = list(reversed(spans))
    while stack:
        a, b = stack.pop()
        if b - a > limit:
            lo, hi = a + limit // 3, b - limit // 3  # split away from the edges
            cut = lo + int(np.argmin(db[lo:hi]))
            stack += [[cut, b], [a, cut]]
        else:
            out.append((a, b))
    return [(a * HOP_S, b * HOP_S) for a, b in out if (b - a) * HOP_S >= min_len]


def _cut_wav(src: Path, dst: Path, start: float, end: float) -> Path:
    with wave.open(str(src)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        w.setpos(int(start * sr))
        frames = w.readframes(int((end - start) * sr))
    with wave.open(str(dst), "wb") as o:
        o.setnchannels(ch)
        o.setsampwidth(sw)
        o.setframerate(sr)
        o.writeframes(frames)
    return dst


def transcribe(wav: Path, client: MetaClient, keywords: list[str] | None = None, pad: float = 0.15,
               workers: int = 8) -> list[Turn]:
    """One PUSH_TO_TALK call per utterance, in parallel; empty utterances (noise) are dropped."""
    spans = utterances(wav)
    tmp = wav.parent / "utt"
    tmp.mkdir(exist_ok=True)

    def run(k: int) -> str:
        a, b = spans[k]
        f = _cut_wav(wav, tmp / f"u{k:03d}.wav", max(0.0, a - pad), b + pad)
        return clean(client.transcribe(f, mode="PUSH_TO_TALK", keywords=keywords).text)

    with ThreadPoolExecutor(workers) as ex:
        texts = list(ex.map(run, range(len(spans))))
    turns = []
    for (a, b), text in zip(spans, texts):
        if text:
            turns.append(Turn(len(turns), round(a, 2), round(b, 2), text))
    return turns


def to_json(turns: list[Turn]) -> list[dict]:
    return [asdict(t) for t in turns]


def from_json(rows: list[dict]) -> list[Turn]:
    return [Turn(**r) for r in rows]
