"""Day 1 API spike: latency, quality, and cost of each Meta model on airplane footage.

Usage: python spike/api_spike.py <test> [<test> ...]   (tests: see TESTS below)
Inputs are prepared in out/spike/ (wav, downscaled mp4, keyframes); each run appends one
JSON line per test to out/spike/results.jsonl and writes the raw output next to it.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from apprentice.models.meta import SAM, SPARK, SPARK_AUDIO, MetaClient, data_url, image_part

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "spike"
FOOT = ROOT / "footage"

# $ per 1M tokens (standard mode), per frame/image, per hour of audio
SPARK_IN, SPARK_CACHED, SPARK_OUT = 1.25, 0.15, 4.25
SAM_IMAGE, SAM_FRAME = 2.50 / 1000, 0.20 / 1000
ASR_HOUR = 0.18

c = MetaClient()


def spark_cost(u: dict) -> float:
    inp = u.get("prompt_tokens", u.get("input_tokens", 0))
    cached = (u.get("prompt_tokens_details") or u.get("input_tokens_details") or {}).get("cached_tokens", 0)
    out = u.get("completion_tokens", u.get("output_tokens", 0))
    return ((inp - cached) * SPARK_IN + cached * SPARK_CACHED + out * SPARK_OUT) / 1e6


def log(name: str, **row) -> None:
    row = {"test": name, "ts": time.strftime("%H:%M:%S"), **row}
    with open(OUT / "results.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: v for k, v in row.items() if k != "output"}, indent=1))


def save(name: str, text: str) -> None:
    (OUT / f"{name}.txt").write_text(text)


# --- Muse Voice Transcribe -------------------------------------------------------------

def t_transcribe(wav: str = "expert1.wav", name: str = "transcribe"):
    r = c.transcribe(OUT / wav)
    d = r.data
    dur = d.get("audioDurationMs", 0) / 1000
    turns = d.get("turns", [])
    save(name, json.dumps(d, indent=1))
    log(name, latency_s=round(r.latency_s, 2), audio_s=dur, rtf=round(r.latency_s / max(dur, 1e-9), 3),
        turns=len(turns), chars=len(r.text), cost=round(dur / 3600 * ASR_HOUR, 5),
        first_turns=[(t["startMs"], t["endMs"], t["transcript"][:80]) for t in turns[:6]])


def t_transcribe_norm():
    t_transcribe("expert1_norm.wav", "transcribe_norm")


# --- Muse Spark: images ----------------------------------------------------------------

def t_spark_image_label():
    """Read fine text from a 1080x1440 SDK photo (the 'photo for detail' path)."""
    r = c.chat([{"role": "user", "content": [
        {"type": "text", "text": "Transcribe every piece of text visible on this label exactly. Then say what the object is."},
        image_part(FOOT / "photo1_label.png")]}], max_tokens=4000, reasoning_effort="low")
    save("spark_image_label", r.text)
    log("spark_image_label", latency_s=round(r.latency_s, 2), usage=r.usage, cost=round(spark_cost(r.usage), 5),
        output=r.text)


def t_spark_image_state():
    """Describe workpiece state in a single expert keyframe (what a per-step check sees)."""
    r = c.chat([{"role": "user", "content": [
        {"type": "text", "text": "This is a first-person frame of someone folding a paper airplane. "
                                 "Describe the exact current state of the paper (which folds are done) in 2 sentences."},
        image_part(OUT / "expert1_t60.jpg")]}], max_tokens=4000, reasoning_effort="low")
    save("spark_image_state", r.text)
    log("spark_image_state", latency_s=round(r.latency_s, 2), usage=r.usage, cost=round(spark_cost(r.usage), 5),
        output=r.text)


def _done_check(img: Path, effort: str):
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"done": {"type": "boolean"}, "confidence": {"type": "number"},
                             "reason": {"type": "string"}},
              "required": ["done", "confidence", "reason"]}
    return c.chat([{"role": "user", "content": [
        {"type": "text", "text": "Step: 'Fold the top two corners down to the center crease so the top forms a point.' "
                                 "Is this step completed in the image?"},
        image_part(img)]}], max_tokens=2000, reasoning_effort=effort,
        response_format={"type": "json_schema", "json_schema": {"name": "check", "strict": True, "schema": schema}})


def t_spark_done_negative():
    """Same check on early novice frames where the corners are not folded yet; should be done=false."""
    for f in ("novice_t2.jpg", "novice_t8.jpg"):
        r = _done_check(OUT / f, "minimal")
        log(f"spark_done_negative_{f[:-4]}", latency_s=round(r.latency_s, 2), cost=round(spark_cost(r.usage), 5),
            output=r.text)


def t_spark_done_check():
    """Live-tick shaped call: low-res 7 fps stream frame, yes/no structured output. Latency is what matters."""
    for effort in ("minimal", "low"):
        lat = []
        outs = []
        usage = {}
        for _ in range(3):
            r = _done_check(OUT / "novice_t60.jpg", effort)
            lat.append(round(r.latency_s, 2))
            outs.append(r.text)
            usage = r.usage
        log(f"spark_done_check_{effort}", latencies_s=lat, usage=usage, cost=round(spark_cost(usage), 5),
            output=outs)


# --- Muse Spark: video -----------------------------------------------------------------

COMPILE_PROMPT = """You are watching a first-person recording of an expert doing a physical task while narrating.
Segment it into the procedure's steps. For each step return:
- t_start, t_end (seconds from the start of the video)
- instruction (imperative, one sentence)
- why (the reason the expert gives or implies; empty if none)
- done_check (what must be visibly true when the step is complete)
- known_mistakes (mistakes a novice could make here)
- quote (the expert's own words for this step, verbatim from the audio)
Return JSON: {"task": str, "steps": [...]}"""


def t_spark_video_compile():
    fid = c.upload(OUT / "expert2_540p10.mp4")
    try:
        t0 = time.perf_counter()
        r = c.respond_stream([{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": COMPILE_PROMPT},
            {"type": "input_video", "file_id": fid}]}],
            model=SPARK, max_output_tokens=16000, reasoning={"effort": "medium"},
            text={"format": {"type": "json_object"}})
        save("spark_video_compile", r.text)
        log("spark_video_compile", latency_s=round(r.latency_s, 2), first_token_s=r.first_token_s,
            upload_plus_call_s=round(time.perf_counter() - t0, 2), usage=r.usage,
            cost=round(spark_cost(r.usage), 5), output=r.text)
    finally:
        c.delete_file(fid)


def t_spark_audio():
    """Spark 1.2 audio transcription with timestamps, as a fallback to Voice Transcribe turn-level timing."""
    import base64
    b64 = base64.b64encode((OUT / "expert2_norm.wav").read_bytes()).decode()
    r = c.chat([{"role": "user", "content": [
        {"type": "text", "text": "Transcribe this narration. Prefix each sentence with its start time as [mm:ss.s]."},
        {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]}],
        model=SPARK_AUDIO, max_tokens=8000, reasoning_effort="low")
    save("spark_audio", r.text)
    log("spark_audio", latency_s=round(r.latency_s, 2), usage=r.usage, cost=round(spark_cost(r.usage), 5),
        output=r.text)


# --- SAM 3.1 ---------------------------------------------------------------------------

BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>")


def _sam(prompt: str, media: dict, **meta) -> tuple:
    r = c.respond_stream([{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": prompt}, media]}], model=SAM,
        **({"metadata": meta} if meta else {}))
    frames = {}
    for line in r.text.splitlines() or r.raw_lines:
        m = re.match(r"<(\d+)f>", line)
        if m:
            frames[int(m.group(1))] = [b.groups() for b in BOX.finditer(line)]
    return r, frames


def t_sam_image():
    for prompt in ("paper", "hand"):
        r, frames = _sam(prompt, {"type": "input_image", "image_url": data_url(OUT / "expert1_t60.jpg")},
                         include_confidence="true", mask_encoding="one_bit")
        save(f"sam_image_{prompt}", r.text)
        log(f"sam_image_{prompt}", latency_s=round(r.latency_s, 2), first_token_s=r.first_token_s,
            boxes=frames.get(0, []), usage=r.usage, cost=SAM_IMAGE, bytes=len(r.text))


def t_sam_video():
    """Track the paper through the 7 fps novice stream clip (~800 frames)."""
    fid = c.upload(FOOT / "plane_novice_7fps.mp4")
    try:
        r, frames = _sam("paper", {"type": "input_video", "file_id": fid},
                         include_confidence="true", mask_encoding="one_bit")
        save("sam_video_paper", r.text)
        n = r.usage.get("video_frames_processed") or len(frames)
        ids = sorted({b for f in frames.values() for b in range(len(f))})
        log("sam_video_paper", latency_s=round(r.latency_s, 2), first_token_s=r.first_token_s,
            frames_with_match=len(frames), frames_billed=n, max_objects=max((len(v) for v in frames.values()), default=0),
            usage=r.usage, cost=round(n * SAM_FRAME, 4), bytes=len(r.text), ids=ids[:10])
    finally:
        c.delete_file(fid)


TESTS = {k[2:]: v for k, v in globals().items() if k.startswith("t_")}

if __name__ == "__main__":
    for name in sys.argv[1:] or TESTS:
        try:
            TESTS[name]()
        except Exception as e:  # keep going; failures are spike findings too
            log(name, error=f"{type(e).__name__}: {e}"[:1000])
