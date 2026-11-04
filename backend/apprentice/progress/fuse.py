"""The task progress shown to the novice: the step tracker decides which step, DINOv2 how far into it.

The DINOv2 progress model alone is a poor whole-task gauge on a novice. On demo/catch5 it read 64%
through step 3 (the expert was 45-53% through the task there), and fell to 24-41% on the throw,
because a sheet folded differently or a plane in the air looks like an earlier state or like
nothing the expert did. The step tracker, judged by Muse Spark, is right about the step. So:

  shown = lo_k + f * (hi_k - lo_k)

where [lo_k, hi_k] is step k's share of the expert's run (its t_start/t_end in procedure.json) and
f is how far the DINOv2 estimate is through the same step's band in the model's own label space
(the progress label at the expert's step boundaries), capped below 1 so only an advance completes
a step. If that band has no width (a step after the work is finished, like the throw) f follows
time against the expert's duration for the step; otherwise f is never below half that pace, so
the gauge creeps while the work is out of view, and never above twice that pace, so it does not
jump when a step starts on work that already looks further along. f never goes down within a step, so the gauge
only moves forward; a finished task reads 100%.
"""

from __future__ import annotations

import numpy as np

from apprentice.progress.model import ProgressModel

CAP = 0.9           # an unconfirmed step never shows as complete
TIME_WEIGHT = 0.5   # with the work out of view or unlike the expert's, the gauge still creeps
MAX_PACE = 2.0      # and never fills faster than twice the expert's pace (no jump on entering a step)
MIN_BAND = 0.02     # narrower than this in label space and DINOv2 can't place the work within it


class TaskProgress:
    def __init__(self, proc: dict, model: ProgressModel, run: str):
        steps = proc["steps"]
        t0, t1 = steps[0]["t_start"], steps[-1]["t_end"]
        ends = [s["t_end"] for s in steps]
        self.dur = [max(1.0, s["t_end"] - s["t_start"]) for s in steps]
        # Display bands: each step's share of the expert's time
        self.show = np.array([0.0] + [(e - t0) / (t1 - t0) for e in ends])
        # Label-space bands: the model's progress label for `run` at each step boundary
        r = model.runs.index(run) if run in model.runs else len(model.runs) - 1
        m = model.bank.run == r
        o = np.argsort(model.bank.t[m])
        t, p = model.bank.t[m][o], model.bank.p[m][o]
        self.label = np.r_[0.0, np.interp(ends, t, p)]
        self.label[-1] = 1.0
        self.k = -1
        self.entered = 0.0
        self.f = 0.0

    def update(self, t: float, k: int, finished: bool, p: float | None) -> float:
        if finished:
            return 1.0
        if k != self.k:
            self.k, self.entered, self.f = k, t, 0.0
        lo, hi = self.label[k], self.label[k + 1]
        pace = (t - self.entered) / self.dur[k]          # time against the expert's for this step
        if hi - lo >= MIN_BAND and p is not None:
            f = min(max((p - lo) / (hi - lo), TIME_WEIGHT * pace), MAX_PACE * pace)
        else:
            f = pace
        self.f = max(self.f, float(np.clip(f, 0.0, CAP)))
        return float(self.show[k] + self.f * (self.show[k + 1] - self.show[k]))

    def label_at(self, shown: float) -> float:
        """The model's progress label for a shown value, for finding the expert frame at that point."""
        return float(np.interp(shown, self.show, self.label))
