"""Spoken guidance from Meta's MMS-TTS (open weights, runs on the laptop in about a second per line).

The novice hears a clean synthetic voice, not the expert's room-noise recording; the expert's own
words stay on screen as text. The model loads on first use (about 10 s) and is shared by all callers.
"""

from __future__ import annotations

import io
import threading
import wave

import numpy as np

MODEL = "facebook/mms-tts-eng"


class Speaker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = self._tok = None

    def _load(self) -> None:
        import torch  # noqa: F401  (transformers needs it on the path)
        from transformers import AutoTokenizer, VitsModel
        self._tok = AutoTokenizer.from_pretrained(MODEL)
        self._model = VitsModel.from_pretrained(MODEL).eval()

    def warm(self) -> None:
        with self._lock:
            if self._model is None:
                self._load()

    def say(self, text: str) -> bytes:
        """16-bit mono WAV of `text`."""
        import torch
        with self._lock:
            if self._model is None:
                self._load()
            with torch.no_grad():
                wav = self._model(**self._tok(text, return_tensors="pt")).waveform[0].numpy()
        pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(self._model.config.sampling_rate)
            f.writeframes(pcm.tobytes())
        return buf.getvalue()


_speaker: Speaker | None = None


def speaker() -> Speaker:
    global _speaker
    if _speaker is None:
        _speaker = Speaker()
    return _speaker
