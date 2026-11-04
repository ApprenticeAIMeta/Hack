"""Meta's Omnilingual ASR (open weights, 1,600+ languages) as a long-lived local worker.

Runs in its own environment (fairseq2 pins torch 2.8 and NumPy 1, which the app's environment does
not use): `~/.venvs/omni`, with omnilingual-asr, torchaudio 2.8, pyarrow<18 and soundfile. The app
starts it with `OmniASR()` and sends it WAV paths; this file is also the worker's entry point.

Protocol, one JSON object per line: in {"wav": path}, out {"text": str, "s": seconds} or {"error": str}.
The CTC 300M model (1.3 GB, downloaded on first start to ~/.cache/fairseq2) needs no language tag
and transcribes a short question in about 6 s on the M2's CPU, against about 1 s for Muse Voice
Transcribe, so the app uses it as a fallback, or first for non-English sessions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

MODEL_CARD = "omniASR_CTC_300M"
VENV_PYTHON = Path(os.environ.get("OMNI_PYTHON", Path.home() / ".venvs/omni/bin/python"))
SNDFILE_DIR = VENV_PYTHON.parent.parent / "lib" / "sndfile"   # libsndfile.1.dylib -> soundfile's bundled copy


class OmniASR:
    """The app's side: starts the worker in the background once, then transcribes on request.
    available() is False while the model loads, or if the worker environment is missing."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        """Load the model in the background (about 15 s once downloaded); safe to call repeatedly."""
        if not self._started and VENV_PYTHON.exists():
            self._started = True
            threading.Thread(target=self._start, daemon=True).start()

    def _start(self) -> None:
        env = {**os.environ, "DYLD_LIBRARY_PATH": str(SNDFILE_DIR), "PYTHONWARNINGS": "ignore"}
        try:
            self._proc = subprocess.Popen([str(VENV_PYTHON), "-u", __file__], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
            if self._read().get("ready"):                # {"ready": true} once the model is loaded
                self._ready.set()
                print("omnilingual asr ready", flush=True)
        except Exception as e:
            print(f"omnilingual asr unavailable: {e}", flush=True)

    def available(self) -> bool:
        return self._ready.is_set() and self._proc is not None and self._proc.poll() is None

    def transcribe(self, wav: Path) -> str | None:
        if not self.available():
            return None
        with self._lock:
            self._proc.stdin.write(json.dumps({"wav": str(wav)}) + "\n")
            self._proc.stdin.flush()
            out = self._read()
        return out.get("text")

    def _read(self) -> dict:
        """The worker's next JSON line, skipping anything else the libraries print."""
        while True:
            line = self._proc.stdout.readline()
            if not line:
                return {}
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


def _serve() -> None:
    from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline
    pipe = ASRInferencePipeline(model_card=MODEL_CARD)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            t = time.perf_counter()
            text = pipe.transcribe([req["wav"]], batch_size=1)[0]
            print(json.dumps({"text": text, "s": round(time.perf_counter() - t, 2)}), flush=True)
        except Exception as e:                            # keep serving; the app falls back
            print(json.dumps({"error": str(e)[:300]}), flush=True)


if __name__ == "__main__":
    _serve()
