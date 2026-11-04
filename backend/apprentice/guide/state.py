"""Step tracker: turns per-frame progress estimates and Spark checks into guidance events.

Progress decides when to ask; Spark decides. A locate check shows Spark the expert's end state
for every step and asks how many steps the novice's workpiece has completed (asking "is step k
done?" fails once the novice has moved past k and the shape no longer matches k's picture).
Steps move forward only, when two consecutive locate answers agree the novice is past the current
step, or one step at a time on a single very confident answer about a still that progress backs up; a step the novice skipped or already
did is passed without ever being read out.

Mistake triggers, both answered by a Spark mistake check before anything is said:
  regression  smoothed progress falls well below its recent peak (the work went backwards)
  novelty     the workpiece suddenly looks less like any expert frame than it has been (a state the
              experts never showed). Relative to the run's own recent level, because a novice's
              room and lighting already make every frame less similar than expert-to-expert.

Checks run asynchronously through an asker: `ask(kind, step, frame, t)` returns a handle whose
`poll(now)` gives (arrival_time, Check) once the answer is in. SimAsker (replay) calls Spark
right away and releases the answer `latency` seconds of video time later; the live session
submits to a thread pool. Replay passes video time; live passes wall-clock time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from apprentice.guide.policy import Check


@dataclass
class Config:
    near_end: float = 0.5        # ask "done?" once progress is within this fraction of the step's width from its end
    poll_s: float = 8.0          # fallback: ask "done?" at least this often
    min_gap_s: float = 2.0       # between checks of the same kind
    done_conf: float = 0.6       # locate answers below this are ignored
    done_fast_conf: float = 0.85 # one locate answer this confident about a still moves on by one step
                                 # (on stream frames such answers were exact only 2 of 10 times: docs/stills-test.md)
    fast_gate: float = 0.2       # a one-answer advance also needs progress within this of the step's end
    drop: float = 0.12           # regression: this far below the recent peak...
    drop_ticks: int = 3          # ...for this many ticks
    peak_window_s: float = 20.0
    novelty_drop: float = 0.15   # novelty: similarity this far below its recent median...
    novelty_ticks: int = 3       # ...for this many ticks
    novelty_window: int = 60
    mistake_conf: float = 0.75   # a warning needs two positive mistake checks in a row, at any confidence
    warn_cooldown_s: float = 12.0
    mistake_poll_s: float = 15.0 # also look for mistakes this often while a step is under way


@dataclass
class Event:
    t: float
    kind: str                    # instruct | step_done | check | warn | complete
    step: int
    data: dict = field(default_factory=dict)


class Handle(Protocol):
    def poll(self, now: float) -> tuple[float, Check] | None: ...


Asker = Callable[[str, int, np.ndarray, float], Handle]


class _Ready:
    def __init__(self, arrival: float, res: Check):
        self.arrival, self.res = arrival, res

    def poll(self, now: float):
        return (self.arrival, self.res) if now >= self.arrival else None


class SimAsker:
    """Synchronous checks with simulated latency, for replay."""

    def __init__(self, locate: Callable[[np.ndarray], Check], mistake: Callable[[int, np.ndarray], Check]):
        self.locate, self.mistake = locate, mistake

    def __call__(self, kind: str, step: int, frame: np.ndarray, t: float) -> Handle:
        res = self.locate(frame) if kind == "done" else self.mistake(step, frame)
        return _Ready(t + res.latency_s, res)


class Tracker:
    def __init__(self, ranges: list[tuple[float, float]], ask: Asker, cfg: Config | None = None):
        self.ranges = ranges
        self.ask = ask
        self.cfg = cfg or Config()
        self.k = 0
        self.finished = False
        self.instructed = False
        self.ahead: int | None = None   # previous locate answer that was past the current step
        self.here = 0                   # consecutive locate answers placing the novice on this step
        self.suspect = False      # one positive mistake check seen; recheck before warning
        self.pending: list[tuple[float, str, int, Handle]] = []  # (asked at, kind, step, handle)
        self.last_ask = {"done": -1e9, "mistake": -1e9}
        self.last_warn = -1e9
        self.asked_for = False     # the novice pressed "check my step": ask now, skipping the gaps
        self.manual_at = -1.0      # when that check was asked; its answer needs no progress agreement
        self.look = False          # a mistake check is wanted now (does not pre-arm the warning)
        self.peak = deque()      # (t, p)
        self.low_ticks = 0
        self.novel_ticks = 0
        self.sims = deque(maxlen=self.cfg.novelty_window)
        self.events: list[Event] = []
        self._sent = 0

    # -- helpers -------------------------------------------------------------------------

    def _emit(self, t: float, kind: str, step: int, **data) -> None:
        self.events.append(Event(round(t, 2), kind, step, data))

    def _ask(self, t: float, kind: str, frame: np.ndarray) -> None:
        self.pending.append((t, kind, self.k, self.ask(kind, self.k, frame, t)))
        self.last_ask[kind] = t

    def _busy(self, kind: str) -> bool:
        return any(p[1] == kind for p in self.pending)

    def _enter(self, t: float, k: int) -> None:
        self.k, self.ahead, self.suspect, self.here = k, None, False, 0
        self.last_ask["done"] = -1e9
        self.instructed = True          # say the step as it begins; the novice should never wait for a check
        self._emit(t, "instruct", k)

    # -- results -------------------------------------------------------------------------

    def _deliver(self, now: float, p: float | None) -> None:
        due, waiting = [], []
        for asked, kind, step, h in self.pending:
            got = h.poll(now)
            if got:
                due.append((asked, kind, step, got))
            else:
                waiting.append((asked, kind, step, h))
        self.pending = waiting
        for asked, kind, step, (arrival, res) in sorted(due, key=lambda x: x[3][0]):
            self._emit(arrival, "check", step, check=kind, asked=round(asked, 2), yes=res.yes, completed=res.completed,
                       unclear=res.unclear, conf=res.confidence, reason=res.reason, source=res.source,
                       latency_s=round(res.latency_s, 2), cost=res.cost)
            if step != self.k or self.finished:
                continue                                  # stale: we moved on
            if kind == "done":
                if res.unclear or res.confidence < self.cfg.done_conf:
                    continue
                n = res.completed
                if n > self.k:
                    self.here = 0
                    manual = asked == self.manual_at   # hands clear, fresh still: the novice vouched for it
                    # Stills answering two steps ahead have been one-step-ahead reads of work in progress
                    # (guided1: "wings folded" while the plane was being folded in half), so one answer
                    # may only move to the next step
                    fast = res.source == "photo" and res.confidence >= self.cfg.done_fast_conf and n == self.k + 1 and (
                        manual or (p is not None and p >= self.ranges[self.k][1] - self.cfg.fast_gate))
                    if fast or self.ahead is not None:
                        target = self.k + 1   # one step at a time: answers jump around, so never skip ahead
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
                    self.here += 1
                    if not self.instructed:
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
        if not self.instructed:            # first frame: say step 1
            self.instructed = True
            self._emit(t, "instruct", self.k)
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
        if (due or self.asked_for) and not self._busy("done"):
            if self.asked_for:
                self.asked_for = False
                self.manual_at = t
                self.look = True             # and look for a mistake on the same still
            self._ask(t, "done", frame)

        # mistake check on regression or novelty
        triggered = (self.look or self.suspect or self.low_ticks >= cfg.drop_ticks or self.novel_ticks >= cfg.novelty_ticks
                     or (self.instructed and t - self.last_ask["mistake"] >= cfg.mistake_poll_s))
        if (triggered and not self._busy("mistake") and t - self.last_ask["mistake"] >= cfg.min_gap_s
                and t - self.last_warn >= cfg.warn_cooldown_s):
            self._ask(t, "mistake", frame)
            self.look = False
            self.low_ticks = self.novel_ticks = 0

    def flush(self, t: float) -> None:
        """Deliver everything still in flight (end of a replay)."""
        self._deliver(t + 60, None)

    def drain(self) -> list[Event]:
        """Events since the last drain (live sessions push these to clients)."""
        out = self.events[self._sent:]
        self._sent = len(self.events)
        return out


def step_ranges(proc: dict) -> list[tuple[float, float]]:
    """Each step's span in progress units, from the compiled expert run's step times."""
    t0, t1 = proc["steps"][0]["t_start"], proc["steps"][-1]["t_end"]
    return [((s["t_start"] - t0) / (t1 - t0), (s["t_end"] - t0) / (t1 - t0)) for s in proc["steps"]]
