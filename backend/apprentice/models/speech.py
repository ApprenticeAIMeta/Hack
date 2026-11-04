"""Spoken guidance from Meta's MMS-TTS (open weights, one model per language, about a second per
line on the laptop).

The novice hears a clean synthetic voice in their own language, not the expert's room-noise
recording; the expert's own words stay on screen as text. Each language's model loads on first
use (about 10 s) and is shared by all callers.
"""

from __future__ import annotations

import io
import threading
import wave

import numpy as np

# Two-letter codes the app accepts, mapped to MMS-TTS checkpoints (ISO 639-3). Any other code is
# taken as an ISO 639-3 id directly, e.g. "ben", "tam", "swh".
LANGS = {
    "en": "eng", "hi": "hin", "es": "spa", "fr": "fra", "de": "deu", "pt": "por", "it": "ita",
    "ru": "rus", "ar": "ara", "tr": "tur", "vi": "vie", "id": "ind", "pl": "pol", "nl": "nld",
    "ko": "kor", "ta": "tam", "te": "tel", "bn": "ben", "mr": "mar", "gu": "guj", "ur": "urd",
    "sw": "swh", "tl": "tgl", "th": "tha", "uk": "ukr", "ro": "ron", "el": "ell", "hu": "hun",
}
NAMES = {"en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
         "it": "Italian", "ru": "Russian", "ar": "Arabic", "tr": "Turkish", "vi": "Vietnamese", "id": "Indonesian",
         "pl": "Polish", "nl": "Dutch", "ko": "Korean", "ta": "Tamil", "te": "Telugu", "bn": "Bengali",
         "mr": "Marathi", "gu": "Gujarati", "ur": "Urdu", "sw": "Swahili", "tl": "Tagalog", "th": "Thai",
         "uk": "Ukrainian", "ro": "Romanian", "el": "Greek", "hu": "Hungarian"}


def model_id(lang: str) -> str:
    return f"facebook/mms-tts-{LANGS.get(lang, lang)}"


class Speaker:
    def __init__(self, lang: str = "en") -> None:
        self.lang = lang
        self._lock = threading.Lock()
        self._model = self._tok = None

    def _load(self) -> None:
        import torch  # noqa: F401  (transformers needs it on the path)
        from transformers import AutoTokenizer, VitsModel
        self._tok = AutoTokenizer.from_pretrained(model_id(self.lang))
        self._model = VitsModel.from_pretrained(model_id(self.lang)).eval()

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
            ids = self._tok(text.strip() or ".", return_tensors="pt")
            if ids["input_ids"].shape[-1] == 0:    # nothing the model can voice (empty, digits only, other script)
                wav = np.zeros(int(0.2 * self._model.config.sampling_rate), dtype=np.float32)
            else:
                with torch.no_grad():
                    wav = self._model(**ids).waveform[0].numpy()
        pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(self._model.config.sampling_rate)
            f.writeframes(pcm.tobytes())
        return buf.getvalue()


_speakers: dict[str, Speaker] = {}
_registry_lock = threading.Lock()


def speaker(lang: str = "en") -> Speaker:
    with _registry_lock:
        if lang not in _speakers:
            _speakers[lang] = Speaker(lang)
        return _speakers[lang]
