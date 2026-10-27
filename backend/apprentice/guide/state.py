"""Step tracker: turns per-frame progress estimates and Spark checks into guidance events.

Progress decides when to ask; Spark decides. A locate check shows Spark the expert's end state
for every step and asks how many steps the novice's workpiece has completed (asking "is step k
done?" fails once the novice has moved past k and the shape no longer matches k's picture).
Steps move forward only, and only when two consecutive locate answers agree the novice is past
the current step; a step the novice skipped or already did is passed without ever being read out.

Mistake triggers, both answered by a Spark mistake check before anything is said:
  regression  smoothed progress falls well below its recent peak (the work went backwards)
  novelty     the workpiece suddenly looks less like any expert frame than it has been (a state the
              experts never showed). Relative to the run's own recent level, because a novice's
              room and lighting already make every frame less similar than expert-to-expert.

Checks run asynchronously: a result arrives `latency` seconds after its request, as it would
live. Replay passes video time; live passes wall-clock time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from apprentice.guide.policy import Check


@dataclass
class Config:
    near_end: float = 0.5        # ask "done?" once progress is within this fraction of the step's width from its end
    poll_s: float = 8.0          # fallback: ask "done?" at least this often
    min_gap_s: float = 2.0       # between checks of the same kind
    done_conf: float = 0.6       # locate answers below this are ignored
    drop: float = 0.12           # regression: this far below the recent peak...
    drop_ticks: int = 3          # ...for this many ticks
    peak_window_s: float = 20.0
    novelty_drop: float = 0.15   # novelty: similarity this far below its recent median...
    novelty_ticks: int = 3       # ...for this many ticks
    novelty_window: int = 60
    mistake_conf: float = 0.75   # a warning needs two positive mistake checks in a row
    warn_cooldown_s: float = 12.0


@dataclass
class Event:
    t: float
    kind: str                    # instruct | step_done | check | warn | complete
    step: int
    data: dict = field(default_factory=dict)


class Tracker:
    def __init__(self, ranges: list[tuple[float, float]], locate: Callable[[np.ndarray], Check],
                 mistake_check: Callable[[int, np.ndarray], Check], cfg: Config | None = None):
        self.ranges = ranges
        self.locate, self.mistake_check = locate, mistake_check
        self.cfg = cfg or Config()
        self.k = 0
        self.finished = False
        self.instructed = False
        self.ahead: int | None = None   # previous locate answer that was past the current step
        self.suspect = False      # one positive mistake check seen; recheck before warning
        self.pending: list[tuple[float, str, int, Check]] = []   # (arrival, kind, step, result)
        self.last_ask = {"done": -1e9, "mistake": -1e9}
        self.last_warn = -1e9
        self.peak = deque()      # (t, p)
        self.low_ticks = 0
        self.novel_ticks = 0
        self.sims = deque(maxlen=self.cfg.novelty_window)
        self.events: list[Event] = []

    # -- helpers -------------------------------------------------------------------------

    def _emit(self, t: float, kind: str, step: int, **data) -> None:
        self.events.append(Event(round(t, 2), kind, step, data))

    def _ask(self, t: float, kind: str, frame: np.ndarray) -> None:
        res = self.locate(frame) if kind == "done" else self.mistake_check(self.k, frame)
        self.pending.append((t + res.latency_s, kind, self.k, res))
        self.last_ask[kind] = t
        self._emit(t, "check", self.k, check=kind, yes=res.yes, completed=res.completed, unclear=res.unclear,
                   conf=res.confidence, reason=res.reason,
                   latency_s=round(res.latency_s, 2), cost=res.cost)

    def _busy(self, kind: str) -> bool:
        return any(p[1] == kind for p in self.pending)

    def _enter(self, t: float, k: int) -> None:
        self.k, self.ahead, self.instructed, self.suspect = k, None, False, False
        self.last_ask["done"] = -1e9

    # -- results -------------------------------------------------------------------------

    def _deliver(self, now: float, p: float | None) -> None:
        due = [x for x in self.pending if x[0] <= now]
        self.pending = [x for x in self.pending if x[0] > now]
        for arrival, kind, step, res in sorted(due, key=lambda x: x[0]):
            if step != self.k or self.finished:
                continue                                  # stale: we moved on
            if kind == "done":
                if res.unclear or res.confidence < self.cfg.done_conf:
                    continue
                n = res.completed
                if n > self.k:
                    if self.ahead is not None:
                        target = min(n, self.ahead)
                        for j in range(self.k, target):
                            self._emit(arrival, "step_done", j, reason=res.reason)
                        if target >= len(self.ranges):
                            self.finished = True
                            self._emit(arrival, "complete", target - 1)
                        else:
                            self._enter(arrival, target)
                    else:
                        self.ahead = n
                else:
                    self.ahead = None
                    if not self.instructed:               # settled on this step: tell the novice what to do
                        self.instructed = True
                        self._emit(arrival, "instruct", self.k)
            elif kind == "mistake":
                positive = res.yes and res.confidence >= self.cfg.mistake_conf
                if positive and self.suspect:
                    self.suspect = False
                    self.last_warn = arrival
                    self._emit(arrival, "warn", self.k, what=res.reason, utterance_id=res.utterance_id)
                else:
                    self.suspect = positive

    # -- per frame -----------------------------------------------------------------------

    def tick(self, t: float, p: float | None, sim: float | None, frame: np.ndarray) -> None:
        self._deliver(t, p)
        if self.finished:
            return
        cfg = self.cfg
        a, b = self.ranges[self.k]

        if p is not None:
            self.peak.append((t, p))
            while self.peak and self.peak[0][0] < t - cfg.peak_window_s:
                self.peak.popleft()
            top = max(q for _, q in self.peak)
            self.low_ticks = self.low_ticks + 1 if top - p >= cfg.drop else 0
        if sim is not None:
            base = float(np.median(self.sims)) if len(self.sims) >= 10 else None
            self.novel_ticks = self.novel_ticks + 1 if base is not None and sim < base - cfg.novelty_drop else 0
            self.sims.append(sim)

        # done check: when progress says the step should be ending, or on the fallback poll
        near = p is not None and p >= b - cfg.near_end * (b - a)
        due = t - self.last_ask["done"] >= (cfg.min_gap_s if near or not self.instructed else cfg.poll_s)
        if due and not self._busy("done"):
            self._ask(t, "done", frame)

        # mistake check on regression or novelty
        triggered = self.suspect or self.low_ticks >= cfg.drop_ticks or self.novel_ticks >= cfg.novelty_ticks
        if (triggered and not self._busy("mistake") and t - self.last_ask["mistake"] >= cfg.min_gap_s
                and t - self.last_warn >= cfg.warn_cooldown_s):
            self._ask(t, "mistake", frame)
            self.low_ticks = self.novel_ticks = 0

    def flush(self, t: float) -> None:
        """Deliver everything still in flight (end of a replay)."""
        self._deliver(t + 60, None)


def step_ranges(proc: dict) -> list[tuple[float, float]]:
    """Each step's span in progress units, from the compiled expert run's step times."""
    t0, t1 = proc["steps"][0]["t_start"], proc["steps"][-1]["t_end"]
    return [((s["t_start"] - t0) / (t1 - t0), (s["t_end"] - t0) / (t1 - t0)) for s in proc["steps"]]
