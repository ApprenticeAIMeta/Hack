"""Compare models on the locate check, on labeled frames, and write docs/open-weights.md.

    python eval/bench_models.py [--frames 24] [--passes 2]     (from backend/)

Frames are what a live check sees (sharpest stream frame, cropped to the workpiece), sampled evenly
and at least MARGIN_S from any labeled step change, so the right answer is not a judgment call.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apprentice.guide.policy import SparkPolicy  # noqa: E402
from apprentice.models.meta import MetaClient, Result  # noqa: E402
from apprentice.progress.align import score  # noqa: E402
from apprentice.progress.model import ProgressModel  # noqa: E402
from apprentice.replay import ROOT, spark_views  # noqa: E402

LABEL = Path(__file__).resolve().parent / "labels" / "novice.json"
MARGIN_S = 3.0
PRICE = (1.25, 0.15, 4.25)    # $ per 1M input, cached input, output (same list price on all three)
FAST_CONF = 0.85              # confidence that moves a step on one answer (guide/state.py)


class Hosted:
    """An OpenAI-compatible host of Spark Open, standing in for MetaClient inside SparkPolicy."""

    def __init__(self, url: str, key_env: str, model: str, effort: str):
        self.model, self.effort = model, effort
        self.http = httpx.Client(base_url=url, timeout=120, headers={"Authorization": f"Bearer {os.environ[key_env]}"})

    def chat(self, messages: list[dict], model: str = "", **kw) -> Result:
        kw.pop("prompt_cache_key", None)
        kw["reasoning_effort"] = self.effort
        t0 = time.perf_counter()
        r = self.http.post("/chat/completions", json={"model": self.model, "messages": messages, **kw})
        dt = time.perf_counter() - t0
        r.raise_for_status()
        data = r.json()
        return Result(data=data, latency_s=dt, text=data["choices"][0]["message"].get("content") or "")


def cost(u: dict) -> float:
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return ((u.get("prompt_tokens", 0) - cached) * PRICE[0] + cached * PRICE[1]
            + u.get("completion_tokens", 0) * PRICE[2]) / 1e6


def frames(n: int) -> list[tuple[float, int, np.ndarray]]:
    lab = json.loads(LABEL.read_text())
    pdir = ROOT / "procedures" / lab["procedure"]
    proc = json.loads((pdir / "procedure.json").read_text())
    video = ROOT / lab["video"]
    s = score(video, "novice", ProgressModel.load(pdir / "progress.npz"), proc["workpiece"])
    views = spark_views(video, s["t"], s["boxes"], s["frames"][0].shape[1])
    done = [x for x in lab["step_done"] if x is not None]
    ok = [i for i in views if s["t"][i] <= lab["end"] and all(abs(s["t"][i] - d) >= MARGIN_S for d in done)]
    picks = [ok[int(j)] for j in np.linspace(0, len(ok) - 1, n)]
    return [(float(s["t"][i]), sum(d <= s["t"][i] for d in done), views[i]) for i in picks]


def run(name: str, client, data: list, passes: int) -> dict:
    pol = SparkPolicy(ROOT / "procedures" / "airplane", client)
    orig = client.chat
    usage: list[dict] = []

    def chat(*a, **k):
        r = orig(*a, **k)
        usage.append(r.usage)
        return r
    client.chat = chat
    rows, errors = [], []
    for p in range(passes):
        for t, truth, view in data:
            try:
                c = pol.locate(view)
                rows.append({"t": t, "pass": p, "truth": truth, "n": c.completed, "conf": c.confidence,
                             "unclear": c.unclear, "lat": c.latency_s})
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}"[:200])
    return {"name": name, "rows": rows, "errors": errors, "cost": sum(map(cost, usage))}


def summary(r: dict) -> dict:
    rows = r["rows"]
    if not rows:
        return {"model": r["name"], "answers": 0, "errors": len(r["errors"]), "error": r["errors"][:1]}
    use = [x for x in rows if not x["unclear"]]
    fast = [x for x in use if x["conf"] >= FAST_CONF]
    lats = sorted(x["lat"] for x in rows)
    a = {(x["t"], x["pass"]): x["n"] for x in rows}
    same = [a[(t, 0)] == a[(t, 1)] for t, p in a if p == 0 and (t, 1) in a]
    return {"model": r["name"], "answers": len(rows), "errors": len(r["errors"]),
            "exact": round(np.mean([x["n"] == x["truth"] for x in use]), 2) if use else None,
            "mean_err": round(np.mean([abs(x["n"] - x["truth"]) for x in use]), 2) if use else None,
            "unclear": sum(x["unclear"] for x in rows),
            "fast_n": len(fast), "fast_exact": round(np.mean([x["n"] == x["truth"] for x in fast]), 2) if fast else None,
            "repeat": round(np.mean(same), 2) if same else None,
            "lat_med": round(st.median(lats), 2), "lat_p90": round(lats[int(0.9 * (len(lats) - 1))], 2),
            "usd_per_check": round(r["cost"] / len(rows), 5)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--passes", type=int, default=2)
    a = ap.parse_args()
    load_dotenv(ROOT / ".env")
    data = frames(a.frames)
    print("truth counts", np.bincount([d[1] for d in data]).tolist(), flush=True)
    models = [("Muse Spark 1.3, Meta API, minimal effort", MetaClient()),
              ("Spark Open, Baseten, low effort", Hosted("https://inference.baseten.co/v1", "BASETEN_API_KEY", "meta-models/muse-spark-open-1026", "low")),
              ("Spark Open, Fireworks, low effort", Hosted("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", "accounts/fireworks/models/muse-spark-open", "low"))]
    with ThreadPoolExecutor(len(models)) as ex:
        results = list(ex.map(lambda m: run(m[0], m[1], data, a.passes), models))
    rows = [summary(r) for r in results]
    print(json.dumps(rows, indent=1, default=float))
    (ROOT / "out" / "bench_models.json").write_text(json.dumps(results, indent=1, default=float))
    md = ("# Open-weight comparison on the locate check\n\nGenerated by `backend/eval/bench_models.py`: "
          f"{a.frames} frames from one labeled novice run, {a.passes} passes each. All frames are at least {MARGIN_S:.0f} s from a "
          "labeled step change. Models ran at the same time. One run from one person, so read this as a screen, not a benchmark.\n\n"
          f"| Model | Exactly right | Mean steps off | Unclear | Exact when confidence >= {FAST_CONF} | Same answer on repeat | Latency median / p90 | $ per check | Errors |\n"
          "|---|---|---|---|---|---|---|---|---|\n")
    for x in rows:
        md += (f"| {x['model']} | {x.get('exact')} | {x.get('mean_err')} | {x.get('unclear')} | {x.get('fast_exact')} (n={x.get('fast_n')}) | "
               f"{x.get('repeat')} | {x.get('lat_med')} / {x.get('lat_p90')} s | {x.get('usd_per_check')} | {x['errors']} |\n")
    (ROOT / "docs" / "open-weights.md").write_text(md)


if __name__ == "__main__":
    main()
