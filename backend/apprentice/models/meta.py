"""Thin client for Meta Model API (https://api.meta.ai/v1).

Every call returns its wall-clock latency so callers can log it; the API key is read
from META_API_KEY in the repo-root .env and never logged.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

BASE_URL = "https://api.meta.ai/v1"
SPARK = "muse-spark-1.3"
SPARK_AUDIO = "muse-spark-1.2"  # 1.3 audio input is not fully supported yet
SAM = "sam-3.1"
TRANSCRIBE = "muse-voice-transcribe-1.0"

# Fallback for Spark when the Meta Model API is unreachable or failing: Meta's open-weights
# Muse Spark Open, hosted on Baseten (OpenAI-compatible). Measured on the locate check in
# docs/open-weights.md: less accurate (0.23 vs 0.37 exact) and slower (6.2 s median), so it is
# only ever a fallback. Too large to run on the laptop itself.
FALLBACK_URL = "https://inference.baseten.co/v1"
FALLBACK_MODEL = "meta-models/muse-spark-open-1026"
FALLBACK_KEY_ENV = "BASETEN_API_KEY"

load_dotenv(Path(__file__).resolve().parents[3] / ".env")


@dataclass
class Result:
    data: dict
    latency_s: float
    text: str = ""
    first_token_s: float | None = None
    raw_lines: list[str] = field(default_factory=list)
    model: str = ""               # which model answered (the fallback is labelled on screen)

    @property
    def usage(self) -> dict:
        return self.data.get("usage") or {}


class MetaClient:
    def __init__(self, api_key: str | None = None, timeout: float = 600.0):
        key = api_key or os.environ.get("META_API_KEY")
        if not key:
            raise RuntimeError("META_API_KEY is not set (put it in the repo-root .env)")
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(timeout, connect=30.0),
        )

    def models(self) -> list[str]:
        r = self._http.get("/models")
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    def chat(self, messages: list[dict], model: str = SPARK, timeout: float | None = None, **kw) -> Result:
        """timeout overrides the client's 600 s default for this call (live checks must not hang the
        guide). A Spark call that cannot reach the Meta API, times out, is rate limited or gets a
        server error is sent once to Muse Spark Open on Baseten instead, when BASETEN_API_KEY is set;
        APPRENTICE_FORCE_FALLBACK=1 skips the Meta API (to test the fallback)."""
        to = httpx.Timeout(timeout, connect=5.0) if timeout is not None else httpx.USE_CLIENT_DEFAULT
        if not os.environ.get("APPRENTICE_FORCE_FALLBACK"):
            t0 = time.perf_counter()
            try:
                r = self._http.post("/chat/completions", json={"model": model, "messages": messages, **kw}, timeout=to)
            except (httpx.TransportError,) as e:          # connect errors and timeouts
                if not self._can_fall_back(model):
                    raise
                print(f"meta api unreachable ({type(e).__name__}); using {FALLBACK_MODEL}", flush=True)
            else:
                if r.status_code < 500 and r.status_code != 429 or not self._can_fall_back(model):
                    dt = time.perf_counter() - t0
                    _raise(r)
                    data = r.json()
                    return Result(data=data, latency_s=dt, text=data["choices"][0]["message"].get("content") or "", model=model)
                print(f"meta api HTTP {r.status_code}; using {FALLBACK_MODEL}", flush=True)
        return self._fallback_chat(messages, timeout, **kw)

    def _can_fall_back(self, model: str) -> bool:
        return model.startswith("muse-spark") and bool(os.environ.get(FALLBACK_KEY_ENV))

    def _fallback_chat(self, messages: list[dict], timeout: float | None, **kw) -> Result:
        key = os.environ.get(FALLBACK_KEY_ENV)
        if not key:
            raise RuntimeError(f"Meta API unavailable and {FALLBACK_KEY_ENV} is not set for the Spark Open fallback")
        kw.pop("prompt_cache_key", None)                  # Meta-only
        if kw.get("reasoning_effort") == "minimal":       # Spark Open's lowest effort is "low"
            kw["reasoning_effort"] = "low"
        # At "low" Spark Open reasons for about 2,000 tokens before answering (a mistake check came back
        # empty at max_tokens 2000, all of it reasoning), so give it room
        kw["max_tokens"] = max(kw.get("max_tokens") or 0, 8000)
        t0 = time.perf_counter()
        r = httpx.post(f"{FALLBACK_URL}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                       json={"model": FALLBACK_MODEL, "messages": messages, **kw},
                       timeout=httpx.Timeout(max(timeout or 120.0, 30.0), connect=5.0))   # slower than the Meta API: a degraded guide beats no answer
        dt = time.perf_counter() - t0
        _raise(r)
        data = r.json()
        return Result(data=data, latency_s=dt, text=data["choices"][0]["message"].get("content") or "", model="muse-spark-open")

    def respond_stream(self, input: list[dict], model: str, **kw) -> Result:
        """Responses API with SSE; collects output_text deltas (one per SAM frame)."""
        t0 = time.perf_counter()
        first = None
        deltas: list[str] = []
        final: dict = {}
        timeout = kw.pop("timeout", None)   # per-call override (live SAM calls must not stall the guide)
        body = {"model": model, "input": input, "stream": True, **kw}
        with self._http.stream("POST", "/responses", json=body, headers={"Accept": "text/event-stream"},
                               timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT) as r:
            if r.status_code >= 400:
                r.read()
                _raise(r)
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line.removeprefix("data: ")
                if payload == "[DONE]":
                    break
                ev = json.loads(payload)
                kind = ev.get("type")
                if kind == "response.output_text.delta":
                    if first is None:
                        first = time.perf_counter() - t0
                    deltas.append(ev.get("delta", ""))
                elif kind in ("response.completed", "response.failed", "response.incomplete"):
                    final = ev.get("response", {})
                    if kind != "response.completed":
                        raise RuntimeError(f"{kind}: {json.dumps(final.get('error') or final)[:500]}")
        return Result(data=final, latency_s=time.perf_counter() - t0, text="".join(deltas),
                      first_token_s=first, raw_lines=deltas)

    def upload(self, path: Path, expires_s: int = 7 * 86400) -> str:
        with open(path, "rb") as f:
            r = self._http.post(
                "/files",
                data={"purpose": "user_data", "expires_after[anchor]": "created_at",
                      "expires_after[seconds]": str(expires_s)},
                files={"file": (path.name, f, _mime(path))},
            )
        _raise(r)
        return r.json()["id"]

    def delete_file(self, file_id: str) -> None:
        self._http.delete(f"/files/{file_id}")

    def transcribe(self, wav: Path, mode: str = "DIARIZATION", keywords: list[str] | None = None) -> Result:
        req = {"mode": mode, "model": TRANSCRIBE, "audioEncoding": "WAV"}
        if keywords:
            req["keywords"] = keywords
        t0 = time.perf_counter()
        with open(wav, "rb") as f:
            r = self._http.post(
                "/asr/transcribe",
                files={"request": (None, json.dumps(req), "application/json"),
                       "audio": (wav.name, f, "audio/wav")},
                headers={"Accept": "application/json"},
            )
        dt = time.perf_counter() - t0
        _raise(r)
        data = r.json()
        return Result(data=data, latency_s=dt, text=data.get("transcript", ""))


def image_part(path: Path) -> dict:
    """Chat Completions image content part as a base64 data URL."""
    return {"type": "image_url", "image_url": {"url": data_url(path)}}


def data_url(path: Path) -> str:
    return f"data:{_mime(path)};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _mime(path: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4",
            ".wav": "audio/wav", ".mp3": "audio/mpeg", ".pdf": "application/pdf"}.get(path.suffix.lower(),
                                                                                    "application/octet-stream")


def _raise(r: httpx.Response) -> None:
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} {r.request.method} {r.request.url.path}: {r.text[:800]}")
