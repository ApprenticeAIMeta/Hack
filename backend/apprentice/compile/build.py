"""Compile one expert session into procedures/<name>/.

    python -m apprentice.compile.build ../footage/plane_expert2_native.mp4 --name airplane

Outputs procedure.json plus clips/step_XX.m4a (expert's voice) and keyframes/step_XX.jpg
(reference done-state frame). Intermediate files (wav, proxy video, transcript, raw Spark output)
are cached in out/compile/<name>/; pass --recompile to call Spark again with a cached transcript.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from apprentice.capture import media
from apprentice.capture.frame_gate import score_video
from apprentice.compile import clips, steps, transcribe
from apprentice.models.meta import MetaClient

ROOT = Path(__file__).resolve().parents[3]
QUIET_DB = -30.0

# standard-mode prices (docs/api-notes.md)
SPARK_IN, SPARK_CACHED, SPARK_OUT, ASR_HOUR = 1.25e-6, 0.15e-6, 4.25e-6, 0.18


def spark_cost(u: dict) -> float:
    cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0)
    return (u.get("input_tokens", 0) - cached) * SPARK_IN + cached * SPARK_CACHED + u.get("output_tokens", 0) * SPARK_OUT


def _clamp_steps(raw: list[dict], duration: float) -> list[dict]:
    out, prev_end = [], 0.0
    for s in raw:
        t0 = min(max(s["t_start"], prev_end), duration)
        t1 = min(max(s["t_end"], t0 + 0.5), duration)
        if t1 - t0 >= 0.5:
            out.append({**s, "t_start": round(t0, 2), "t_end": round(t1, 2)})
            prev_end = t1
    return out


MIN_WORDS = 3  # shorter utterances ("uh", "here") are fillers, not guidance


def build(video: Path, name: str, recompile: bool = False, effort: str = "medium",
          keywords: list[str] | None = None) -> Path:
    client = MetaClient()
    cache = ROOT / "out" / "compile" / name
    dest = ROOT / "procedures" / name
    for d in (cache, dest / "clips", dest / "keyframes"):
        d.mkdir(parents=True, exist_ok=True)
    for old in [*(dest / "clips").glob("*"), *(dest / "keyframes").glob("*")]:
        old.unlink()  # a recompile can produce fewer steps; never keep stale media
    info = media.probe(video)
    cost: dict[str, float] = {}
    t_all = time.perf_counter()

    level = media.audio_level_db(video) if info["has_audio"] else float("-inf")
    if level < QUIET_DB:
        print(f"warning: narration is quiet ({level:.1f} dB mean); transcript may be empty")

    tr_path = cache / "transcript.json"
    if tr_path.exists():
        turns = transcribe.from_json(json.loads(tr_path.read_text()))
    else:
        wav = media.extract_wav(video, cache / "audio.wav")
        turns = transcribe.transcribe(wav, client, keywords=keywords)
        tr_path.write_text(json.dumps(transcribe.to_json(turns), indent=1))
        cost["transcribe"] = info["duration"] / 3600 * ASR_HOUR
    print(f"transcript: {len(turns)} turns")

    proxy = cache / "proxy.mp4"
    if not proxy.exists():
        media.proxy_video(video, proxy)

    raw_path = cache / "steps_raw.json"
    if raw_path.exists() and not recompile:
        raw = json.loads(raw_path.read_text())
    else:
        proc, stats = steps.compile_steps(proxy, turns, client, effort=effort)
        raw = {"procedure": proc, "stats": stats}
        raw_path.write_text(json.dumps(raw, indent=1))
        cost["steps"] = spark_cost(stats["usage"])
        print(f"spark compile: {stats['latency_s']} s")
    proc = raw["procedure"]

    scores, _ = score_video(proxy)
    step_list = _clamp_steps(proc["steps"], info["duration"])
    by_step = clips.assign_turns(step_list, turns)

    out_steps = []
    for i, s in enumerate(step_list):
        sid = f"step_{i + 1:02d}"
        said = [t for t in by_step[i] if len(t.text.split()) >= MIN_WORDS]
        voice = []
        for k, t in enumerate(said):
            f = f"clips/{sid}_{k + 1}.m4a"
            media.cut_audio(video, dest / f, t.start, t.end)
            voice.append({"file": f, "start": t.start, "end": t.end, "text": t.text, "turn_id": t.id})
        ref = clips.reference_frame(scores, s["t_start"], s["t_end"])
        ref_file = None
        if ref:
            media.frame_at(video, ref.t, dest / "keyframes" / f"{sid}.jpg")
            ref_file = {"file": f"keyframes/{sid}.jpg", "t": round(ref.t, 2)}
        out_steps.append({"id": sid, **s, "voice": voice, "reference_frame": ref_file})

    procedure = {
        "name": name,
        "task": proc["task"],
        "workpiece": proc["workpiece"],
        "tools": proc["tools"],
        "source": {"video": video.name, "duration_s": round(info["duration"], 2),
                   "width": info["width"], "height": info["height"], "audio_mean_db": round(level, 1)},
        "steps": out_steps,
        "transcript": transcribe.to_json(turns),
        "compile": {"spark_model": "muse-spark-1.3", "effort": effort, "wall_s": round(time.perf_counter() - t_all, 1),
                    "cost_usd_this_run": {k: round(v, 4) for k, v in cost.items()},
                    "compiled_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
    }
    out = dest / "procedure.json"
    out.write_text(json.dumps(procedure, indent=1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Compile an expert session into a procedure")
    ap.add_argument("video", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--recompile", action="store_true", help="call Spark again even if cached")
    ap.add_argument("--effort", default="medium", choices=["minimal", "low", "medium", "high"])
    ap.add_argument("--keywords", default="", help="comma-separated domain words to bias transcription")
    a = ap.parse_args()
    kw = [k.strip() for k in a.keywords.split(",") if k.strip()] or None
    out = build(a.video.resolve(), a.name, a.recompile, a.effort, kw)
    p = json.loads(out.read_text())
    for s in p["steps"]:
        print(f"{s['id']} [{s['t_start']:6.1f}-{s['t_end']:6.1f}] {s['title']:<28} "
              f"clips={len(s['voice'])}  ref={s['reference_frame'] and s['reference_frame']['t']}")
        for v in s["voice"]:
            print(f"    {v['start']:6.1f}-{v['end']:6.1f}  {v['text']}")
    print(f"-> {out}  {p['compile']}")


if __name__ == "__main__":
    main()
