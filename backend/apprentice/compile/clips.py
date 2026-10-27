"""Attach the expert's own voice and a reference frame to each step.

Voice: each transcript turn goes to the step it overlaps most; a step's clip spans its turns
and its `said` field is their text, so what plays and what is shown are the expert's real words.
Reference frame: the sharpest, steadiest frame near the end of the step, which shows the
workpiece in its done state for that step.
"""

from __future__ import annotations

from apprentice.capture.frame_gate import FrameScore
from apprentice.compile.transcribe import Turn


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def assign_turns(steps: list[dict], turns: list[Turn]) -> dict[int, list[Turn]]:
    """Step index -> turns. A turn with no overlap goes to the nearest step by midpoint."""
    out: dict[int, list[Turn]] = {i: [] for i in range(len(steps))}
    for t in turns:
        scores = [overlap(t.start, t.end, s["t_start"], s["t_end"]) for s in steps]
        if max(scores, default=0) > 0:
            i = scores.index(max(scores))
        else:
            mid = (t.start + t.end) / 2
            i = min(range(len(steps)), key=lambda k: abs(mid - (steps[k]["t_start"] + steps[k]["t_end"]) / 2))
        out[i].append(t)
    return out


def reference_frame(scores: list[FrameScore], t_start: float, t_end: float, tail: float = 0.35) -> FrameScore | None:
    """Best frame in the last `tail` fraction of the step; falls back to the whole step."""
    for lo in (t_end - tail * (t_end - t_start), t_start):
        win = [s for s in scores if lo <= s.t <= t_end]
        if win:
            calm = sorted(win, key=lambda s: s.motion)[: max(1, len(win) // 2)]
            return max(calm, key=lambda s: s.sharpness)
    return None
