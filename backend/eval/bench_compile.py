"""Compile the same expert session with Spark Open on Baseten and compare it with the Muse Spark procedure.

    python eval/bench_compile.py [--effort low]     (from backend/)

Same prompt, schema, proxy video and transcript as apprentice/compile/steps.py. Writes
out/compile/<name>/procedure_spark_open.json. It is not used by the product unless copied over.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apprentice.compile.steps import PROMPT, SCHEMA  # noqa: E402
from apprentice.replay import ROOT  # noqa: E402

MODEL = "meta-models/muse-spark-open-1026"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", default="airplane")
    ap.add_argument("--effort", default="low")
    a = ap.parse_args()
    load_dotenv(ROOT / ".env")
    d = ROOT / "out" / "compile" / a.name
    turns = json.loads((d / "transcript.json").read_text())
    turns = turns["turns"] if isinstance(turns, dict) else turns
    lines = "\n".join(f"[{t['start']:6.1f}-{t['end']:6.1f}] {t['text']}" for t in turns)
    video = base64.b64encode((d / "proxy.mp4").read_bytes()).decode()
    body = {"model": MODEL, "max_tokens": 16000, "reasoning_effort": a.effort,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT.format(turns=lines)},
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{video}"}}]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "procedure", "strict": True, "schema": SCHEMA}}}
    t0 = time.perf_counter()
    r = httpx.post("https://inference.baseten.co/v1/chat/completions", json=body, timeout=600,
                   headers={"Authorization": f"Bearer {os.environ['BASETEN_API_KEY']}"})
    dt = time.perf_counter() - t0
    if r.status_code >= 400:
        raise SystemExit(f"{r.status_code}: {r.text[:500]}")
    data = r.json()
    proc = json.loads(data["choices"][0]["message"]["content"])
    proc["steps"].sort(key=lambda s: s["t_start"])
    (d / "procedure_spark_open.json").write_text(json.dumps(proc, indent=1))
    u = data.get("usage", {})
    print(f"latency {dt:.1f} s, usage {json.dumps(u)}, list cost ${(u.get('prompt_tokens', 0) * 1.25 + u.get('completion_tokens', 0) * 4.25) / 1e6:.3f}")
    base = json.loads((ROOT / "procedures" / a.name / "procedure.json").read_text())
    for name, p in (("Muse Spark", base), ("Spark Open", proc)):
        print(f"\n{name}: {p['task']} | workpiece: {p['workpiece']} | {len(p['steps'])} steps")
        for i, s in enumerate(p["steps"]):
            print(f" {i + 1}. {s['t_start']:.0f}-{s['t_end']:.0f} {s['title']}: {s['instruction']} | done: {s['done_check']} | why: {s['why']}")


if __name__ == "__main__":
    main()
