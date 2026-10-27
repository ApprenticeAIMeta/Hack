"""Expert session video + transcript turns -> ordered procedure steps, via one Muse Spark call.

The proxy video (frames plus embedded audio) carries what the expert did; the transcript turns
are passed as text so step boundaries line up with what the expert said and when. Spark writes
instructions and reasons in its own words; the expert's actual words are attached later from
the turns (see clips.py), never taken from Spark.
"""

from __future__ import annotations

import json
from pathlib import Path

from apprentice.compile.transcribe import Turn
from apprentice.models.meta import SPARK, MetaClient

STEP = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "description": "2-5 word name of the step"},
        "instruction": {"type": "string", "description": "One imperative sentence a novice can follow"},
        "why": {"type": "string", "description": "The reason, as the expert states or clearly implies it; empty if none"},
        "t_start": {"type": "number", "description": "Seconds from the start of the video"},
        "t_end": {"type": "number"},
        "objects": {"type": "array", "items": {"type": "string"},
                    "description": "Short noun phrases for the physical things handled in this step"},
        "done_check": {"type": "string", "description": "What is visibly true of the workpiece when the step is done"},
        "known_mistakes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "instruction", "why", "t_start", "t_end", "objects", "done_check", "known_mistakes"],
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
- Ignore setup chatter and anything not part of the task.
- t_start/t_end are seconds in this video. Use the timed transcript below to place boundaries.
- "why" must come from the expert's words or actions, not general knowledge. Leave it empty rather than invent one.
- done_check describes the workpiece, so it can be verified from a single frame.
- workpiece and objects are short concrete noun phrases (for an object detector), e.g. "paper sheet", "capacitor".

Timed transcript of the expert's narration (seconds):
{turns}"""


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
