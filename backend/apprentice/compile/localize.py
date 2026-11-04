"""Guide a procedure in another language: the expert recorded once, in English; the apprentice
hears and reads the steps in their own language.

    python -m apprentice.compile.localize airplane hi

Muse Spark translates what the novice sees and hears (titles, instructions, the "why", the known
mistakes, the warning lines); MMS-TTS in that language speaks them. Writes
procedures/<name>/lang/<lang>.json and clips/<lang>/*.wav. The checks themselves stay in English:
Spark judges pictures, and the compiled procedure is unchanged.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from apprentice.models.meta import SPARK, MetaClient
from apprentice.compile.lines import say_line
from apprentice.models.speech import NAMES, speaker

ROOT = Path(__file__).resolve().parents[3]

STEP = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "instruction": {"type": "string"},
        "why": {"type": "string"},
        "known_mistakes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "title", "instruction", "why", "known_mistakes"],
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "task": {"type": "string"},
        "stop": {"type": "string", "description": "The single word a supervisor would shout to stop someone"},
        "generic_warn": {"type": "string"},
        "steps": {"type": "array", "items": STEP},
    },
    "required": ["task", "stop", "generic_warn", "steps"],
}

GENERIC = "Stop. Check this against the expert's work before you go on."


def localize(name: str, lang: str, client: MetaClient | None = None) -> Path:
    client = client or MetaClient()
    pdir = ROOT / "procedures" / name
    proc = json.loads((pdir / "procedure.json").read_text())
    language = NAMES.get(lang, lang)
    src = {
        "task": proc["task"],
        "stop": "Stop",
        "generic_warn": GENERIC,
        "steps": [{"id": s["id"], "title": s["title"], "instruction": say_line(s), "why": s["why"],
                   "known_mistakes": s["known_mistakes"]} for s in proc["steps"]],
    }
    prompt = (f"Translate this procedure for a worker who speaks {language}. These lines are spoken aloud and shown "
              "on a phone while the worker's hands are busy, so use short, plain, spoken sentences a tradesperson "
              "would use; keep the meaning exact; keep each known mistake as a short phrase that completes "
              "\"it looks like you're ...\" in that language; keep ids unchanged.\n\n" + json.dumps(src, ensure_ascii=False, indent=1))
    t0 = time.perf_counter()
    r = client.chat([{"role": "user", "content": prompt}], model=SPARK, max_tokens=6000, reasoning_effort="low",
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": "localized", "strict": True, "schema": SCHEMA}})
    out = json.loads(r.text)
    print(f"translated to {language} in {r.latency_s:.1f} s")

    sp = speaker(lang)
    sp.warm()
    clips = pdir / "clips" / lang
    clips.mkdir(parents=True, exist_ok=True)
    by_id = {s["id"]: s for s in out["steps"]}
    for s in proc["steps"]:
        tr = by_id[s["id"]]
        (clips / f"{s['id']}_say.wav").write_bytes(sp.say(tr["instruction"]))
        for j, m in enumerate(tr["known_mistakes"]):
            (clips / f"{s['id']}_warn{j + 1}.wav").write_bytes(sp.say(f"{out['stop']}. {m}. " + (tr["why"] if tr["why"] else "")))
        print(f"{s['id']} {tr['title']}")
    (clips / "generic_warn.wav").write_bytes(sp.say(out["generic_warn"]))
    out["lang"] = lang
    out["language"] = language
    out["rendered_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out["wall_s"] = round(time.perf_counter() - t0, 1)
    dest = pdir / "lang"
    dest.mkdir(exist_ok=True)
    f = dest / f"{lang}.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return f


def main() -> None:
    ap = argparse.ArgumentParser(description="Translate and voice a procedure for another language")
    ap.add_argument("procedure")
    ap.add_argument("lang", help="two-letter code (hi, es, fr, ...) or an ISO 639-3 id MMS-TTS supports")
    a = ap.parse_args()
    print(f"-> {localize(a.procedure, a.lang)}")


if __name__ == "__main__":
    main()
