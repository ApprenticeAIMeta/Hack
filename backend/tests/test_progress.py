"""The task gauge on the bundled airplane procedure and the recorded catch5 run (no model calls)."""

import json
from pathlib import Path

import pytest

from apprentice.progress.fuse import TaskProgress
from apprentice.progress.model import ProgressModel

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "procedures" / "airplane"
RUN = ROOT / "demo" / "catch5" / "ticks.jsonl"


@pytest.fixture(scope="module")
def gauge_parts():
    proc = json.loads((PROC / "procedure.json").read_text())
    model = ProgressModel.load(PROC / "progress.npz")
    return proc, model


def states():
    for line in RUN.read_text().splitlines():
        m = json.loads(line)
        if m.get("type") == "state" and "frame_size" in m:
            yield m


def test_gauge_stays_in_the_current_step_and_never_goes_back(gauge_parts):
    proc, model = gauge_parts
    g = TaskProgress(proc, model, model.runs[0])
    last = 0.0
    for m in states():
        raw = m.get("progress_raw", m["progress"])
        shown = g.update(m["t"], m["step"], m["finished"], raw)
        if not m["finished"]:
            assert g.show[m["step"]] - 1e-9 <= shown <= g.show[m["step"] + 1] + 1e-9
        assert shown >= last - 1e-9
        last = shown


def test_an_unconfirmed_step_never_reads_complete(gauge_parts):
    proc, model = gauge_parts
    g = TaskProgress(proc, model, model.runs[0])
    shown = g.update(0.0, 2, False, 0.99)
    shown = g.update(100.0, 2, False, 0.99)
    assert shown < g.show[3]


def test_a_finished_task_reads_100(gauge_parts):
    proc, model = gauge_parts
    g = TaskProgress(proc, model, model.runs[0])
    assert g.update(5.0, 6, True, 0.1) == 1.0


def test_the_mirror_comes_from_the_same_step(gauge_parts):
    proc, model = gauge_parts
    g = TaskProgress(proc, model, model.runs[0])
    times = []
    for k in range(len(proc["steps"])):
        times.append(model.mirror(g.label_at((g.show[k] + g.show[k + 1]) / 2), model.runs[-1]))
    assert times == sorted(times)


def test_spoken_lines_stay_grammatical():
    from apprentice.compile.lines import say_line, warn_line
    assert warn_line("folding crosswise", "") == "Stop. It looks like you're folding crosswise. Compare it with the expert's."
    assert warn_line("corners not meeting at center", "an even point flies straight.") == \
        "Stop. Check this: corners not meeting at center. An even point flies straight."
    assert say_line({"instruction": "Fold it.", "spoken": ""}) == "Fold it."
    assert say_line({"instruction": "Fold it.", "spoken": "Fold the sheet in half."}) == "Fold the sheet in half."
