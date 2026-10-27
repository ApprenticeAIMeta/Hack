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

load_dotenv(Path(__file__).resolve().parents[3] / ".env")


@dataclass
class Result:
    data: dict
    latency_s: float
    text: str = ""
    first_token_s: float | None = None
    raw_lines: list[str] = field(default_factory=list)

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

    def chat(self, messages: list[dict], model: str = SPARK, **kw) -> Result:
        t0 = time.perf_counter()
        r = self._http.post("/chat/completions", json={"model": model, "messages": messages, **kw})
        dt = time.perf_counter() - t0
        _raise(r)
        data = r.json()
        text = data["choices"][0]["message"].get("content") or ""
        return Result(data=data, latency_s=dt, text=text)

    def respond_stream(self, input: list[dict], model: str, **kw) -> Result:
        """Responses API with SSE; collects output_text deltas (one per SAM frame)."""
        t0 = time.perf_counter()
        first = None
        deltas: list[str] = []
        final: dict = {}
        body = {"model": model, "input": input, "stream": True, **kw}
        with self._http.stream("POST", "/responses", json=body, headers={"Accept": "text/event-stream"}) as r:
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
