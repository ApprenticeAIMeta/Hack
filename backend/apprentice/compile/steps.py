"""Expert session video + transcript turns -> ordered procedure steps, via one Muse Spark call.

The proxy video (frames plus embedded audio) carries what the expert did; the transcript turns
are passed as text so step boundaries line up with what the expert said and when. Spark writes
instructions and reasons in its own words; the expert's actual words are attached later from
the turns (see clips.py), never taken from Spark.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import httpx

from apprentice.compile.transcribe import Turn
from apprentice.models.meta import SPARK, MetaClient

SPARK_OPEN = "meta-models/muse-spark-open-1026"
BASETEN_URL = "https://inference.baseten.co/v1"

STEP = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "description": "2-5 word name of the step"},
        "instruction": {"type": "string", "description": "One imperative sentence a novice can follow, in the expert's names for parts"},
        "spoken": {"type": "string", "description": "What the guide says aloud to the apprentice as this step starts: an imperative "
                   "addressed to them ('Fold the sheet in half the long way, edges matched.'), never the expert's first person "
                   "('I will fold'), at most 22 words, no symbols; for a safety step, add the expert's reason"},
        "why": {"type": "string", "description": "The expert's reason as one short plain sentence (fix filler and grammar, keep the meaning), "
                "e.g. 'An uneven edge makes the plane pull to one side.' Empty if the expert gave none"},
        "t_start": {"type": "number", "description": "Seconds from the start of the video"},
        "t_end": {"type": "number"},
        "objects": {"type": "array", "items": {"type": "string"},
                    "description": "Short noun phrases for the physical things handled in this step"},
        "done_check": {"type": "string", "description": "What is visibly true of the workpiece when the step is done"},
        "expert_warnings": {"type": "array", "items": {"type": "string"},
                            "description": "Mistakes the expert warns about in their own words in this step, each starting with an -ing verb. "
                            "Empty if the expert warned about nothing"},
        "known_mistakes": {"type": "array", "items": {"type": "string"},
                           "description": "Every entry of expert_warnings, then at most two likely mistakes a single photo would show. "
                           "Each starts with an -ing verb, e.g. 'folding crosswise instead of lengthwise'"},
    },
    "required": ["title", "instruction", "spoken", "why", "t_start", "t_end", "objects", "done_check", "expert_warnings", "known_mistakes"],
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task": {"type": "string"},
        "workpiece": {"type": "string", "description": "Short noun phrase for the main object being worked on"},
        "tools": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": STEP},
    },
    "required": ["task", "workpiece", "tools", "steps"],
}

PROMPT = """You are watching a first-person recording from smart glasses of an expert doing a physical task while narrating it.
Turn it into a procedure a novice wearing the same glasses can follow step by step.

Rules:
- Steps are contiguous, in order, and cover the whole task. Split where the workpiece changes state, not where the talking pauses.
- A step is one meaningful change to the workpiece that a novice could check. Merge handling moves (picking up, repositioning, turning over) into the step they serve. Most tasks need 5-10 steps.
- Ignore chatter and anything not part of the task, but safety actions are always steps: switching off power or a breaker,
  verifying it is off, protective gear, isolating a part. Never merge or drop one, and put the expert's safety reason in "why".
- Use the expert's own names for parts, wires, terminals and tools (e.g. "R wire", "C terminal"), corrected for obvious
  transcription slips; spell out what a novice needs to identify them (a wire's colour or terminal letter).
- instruction and spoken are said to someone who cannot see the expert: never "like this", "here", "that one" or "this way".
  Say what the result looks like instead (edges matched, tip pointed, wire fully seated).
- expert_warnings only holds what the expert actually warned about. Likely mistakes you add yourself go in known_mistakes only.
- t_start/t_end are seconds in this video. Use the timed transcript below to place boundaries.
- "why" must come from the expert's words or actions, not general knowledge. Leave it empty rather than invent one.
- done_check describes the workpiece, so it can be verified from a single frame.
- workpiece and objects are short concrete noun phrases (for an object detector), e.g. "paper sheet", "capacitor".

Timed transcript of the expert's narration (seconds):
{turns}"""


def compile_steps_open(video: Path, turns: list[Turn], effort: str = "medium") -> tuple[dict, dict]:
    """The same compile on open weights: Muse Spark Open hosted on Baseten. Too slow for live checks
    (docs/open-weights.md), but a compile has no one waiting on it."""
    lines = "\n".join(f"[{t.start:6.1f}-{t.end:6.1f}] {t.text}" for t in turns) or "(no usable narration)"
    url = "data:video/mp4;base64," + base64.b64encode(video.read_bytes()).decode()
    t0 = time.perf_counter()
    r = httpx.post(f"{BASETEN_URL}/chat/completions", timeout=600,
                   headers={"Authorization": f"Bearer {os.environ['BASETEN_API_KEY']}"},
                   json={"model": SPARK_OPEN, "max_tokens": 16000, "reasoning_effort": effort,
                         "messages": [{"role": "user", "content": [
                             {"type": "text", "text": PROMPT.format(turns=lines)},
                             {"type": "video_url", "video_url": {"url": url}}]}],
                         "response_format": {"type": "json_schema",
                                             "json_schema": {"name": "procedure", "strict": True, "schema": SCHEMA}}})
    r.raise_for_status()
    data = r.json()
    proc = json.loads(data["choices"][0]["message"]["content"])
    proc["steps"].sort(key=lambda s: s["t_start"])
    u = data.get("usage", {})
    usage = {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
             "input_tokens_details": {"cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)}}
    return proc, {"latency_s": round(time.perf_counter() - t0, 1), "usage": usage}


def compile_steps(video: Path, turns: list[Turn], client: MetaClient, effort: str = "medium") -> tuple[dict, dict]:
    """Returns (procedure dict, call stats)."""
    lines = "\n".join(f"[{t.start:6.1f}-{t.end:6.1f}] {t.text}" for t in turns) or "(no usable narration)"
    fid = client.upload(video)
    try:
        r = client.respond_stream(
            [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": PROMPT.format(turns=lines)},
                {"type": "input_video", "file_id": fid}]}],
            model=SPARK, max_output_tokens=16000, reasoning={"effort": effort},
            text={"format": {"type": "json_schema", "name": "procedure", "schema": SCHEMA, "strict": True}})
    finally:
        client.delete_file(fid)
    proc = json.loads(r.text)
    proc["steps"].sort(key=lambda s: s["t_start"])
    return proc, {"latency_s": round(r.latency_s, 1), "usage": r.usage}
