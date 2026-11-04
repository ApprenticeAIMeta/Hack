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
    first_check_s: float = 4.0   # first timed check after a step begins
    throw_settle_s: float = 3.0  # the throw step listens only after this long (time to hear it)
    throw_gone_s: float = 1.5    # the plane out of view this long means it was thrown


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
    def __init__(self, ranges: list[tuple[float, float]], ask: Asker, cfg: Config | None = None,
                 throw_last: bool = False):
        self.ranges = ranges
        self.ask = ask
        self.cfg = cfg or Config()
        # The last step is an action, not a state to photograph (throwing the plane): it is done
        # when the work leaves the camera's view, or when the novice taps
        self.throw_last = throw_last
        self.entered_t = 0.0
        self.last_seen = 0.0
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
        self.trigger = ""           # what asked for the pending check: "tap" (phone) or "settle" (hands off)
        self.manual_trigger = ""
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
        self.entered_t = self.last_seen = t
        # The novice has only just started this step: the first timed check waits FIRST_CHECK_S
        # instead of firing at once (catch3: that check's still waited for the spoken instruction
        # and held the pipeline for about 10 s at the start of every step)
        self.last_ask["done"] = t - self.cfg.poll_s + self.cfg.first_check_s
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
                       latency_s=round(res.latency_s, 2), cost=res.cost, model=res.model,
                       trigger=self.manual_trigger if asked == self.manual_at else "")
            if step != self.k or self.finished:
                continue                                  # stale: we moved on
            if kind == "done":
                if res.unclear or res.confidence < self.cfg.done_conf:
                    continue
                n = res.completed
                if n > self.k:
                    self.here = 0
                    manual = asked == self.manual_at   # hands off or tapped: the novice signalled it is done
                    # Any answer past the current step means at least this step is done. Answers that
                    # read one step ahead are common (a plane being folded in half "has wings"), so the
                    # advance is always to the next step only, never further. A still is trusted once;
                    # a stream frame needs a second answer that also puts the novice past this step.
                    # catch2: requiring exactly k+1 held step 5 from 210 s to 296 s.
                    photo_ok = res.source == "photo" and res.confidence >= self.cfg.done_conf
                    fast = photo_ok or (manual and res.confidence >= self.cfg.done_conf)
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

    def _throwing(self) -> bool:
        return self.throw_last and not self.finished and self.k == len(self.ranges) - 1

    def _finish(self, t: float, reason: str) -> None:
        self._emit(t, "step_done", self.k, reason=reason)
        self.finished = True
        self._emit(t, "complete", self.k)

    def lost(self, t: float) -> None:
        """A tick with the work out of view. On the throw step that is the throw."""
        if self._throwing() and t - self.entered_t >= self.cfg.throw_settle_s and t - self.last_seen >= self.cfg.throw_gone_s:
            self._finish(t, "the plane left the view")

    def tick(self, t: float, p: float | None, sim: float | None, frame: np.ndarray) -> None:
        if self._throwing():
            self.last_seen = t
            self._deliver(t, p)
            if self.asked_for and self.trigger == "tap":
                self.asked_for = False
                self._finish(t, "the novice tapped")
            self.asked_for = False      # hands off the plane is not a throw
            return
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
        # A check the novice asked for (hands off, or a tap) runs at once, even with a timed check in
        # flight; whichever lands first for this step decides, and the other is dropped as stale
        # (catch3: a hands-off check queued 13 s behind a timed one)
        if self.asked_for or (due and not self._busy("done")):
            if self.asked_for:
                self.asked_for = False
                self.manual_at = t
                self.manual_trigger = self.trigger
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

    def goto(self, t: float, k: int) -> None:
        """The novice picked a step on the phone: move there, say it, and forget checks in flight."""
        k = max(0, min(len(self.ranges) - 1, k))
        self.finished = False
        self.pending = []
        self.peak.clear()
        self._emit(t, "goto", k)
        self._enter(t, k)

    def deliver(self, t: float) -> None:
        """Hand out finished checks when there is no frame to tick on (workpiece out of view, SAM
        stalled), so a step advance or a warning is never held back by the next frame."""
        self._deliver(t, None)

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
