"""The step tracker's rules, each from a failure seen in a live run. No model calls: answers are scripted."""

import numpy as np

from apprentice.guide.policy import Check
from apprentice.guide.state import Config, Tracker

FRAME = np.zeros((8, 8, 3), np.uint8)
RANGES = [(i / 4, (i + 1) / 4) for i in range(4)]


class Scripted:
    """An asker whose answers are queued by the test and arrive `latency` seconds after being asked."""

    def __init__(self, latency: float = 1.0):
        self.latency = latency
        self.done: list[Check] = []
        self.mistake: list[Check] = []
        self.asked: list[tuple[str, int, float]] = []

    def __call__(self, kind, step, frame, t):
        self.asked.append((kind, step, t))
        queue = self.done if kind == "done" else self.mistake
        res = queue.pop(0) if queue else Check(False, 0.0, "", 0.0, 0.0, unclear=True)
        arrival = t + self.latency

        class H:
            def poll(self, now):
                return (arrival, res) if now >= arrival else None
        return H()


def located(n, conf=0.9, source="stream"):
    return Check(False, conf, f"{n} done", 1.0, 0.0, completed=n, source=source)


def run(tr, t0, t1, dt=0.5, p=None):
    t = t0
    while t <= t1:
        tr.tick(t, p, None, FRAME)
        t += dt


def kinds(tr, kind):
    return [e for e in tr.events if e.kind == kind]


def test_first_frame_says_step_one():
    tr = Tracker(RANGES, Scripted())
    tr.tick(0.0, None, None, FRAME)
    assert [(e.kind, e.step) for e in tr.events] == [("instruct", 0)]


def test_a_photo_past_the_step_advances_exactly_one_step():
    # catch1: one answer reading three steps ahead skipped steps the novice never did
    ask = Scripted()
    ask.done = [located(3, source="photo")]
    tr = Tracker(RANGES, ask)
    tr.asked_for, tr.trigger = True, "settle"
    run(tr, 0, 3)
    assert tr.k == 1
    assert [e.step for e in kinds(tr, "step_done")] == [0]


def test_a_stream_frame_needs_a_second_answer_before_advancing():
    ask = Scripted()
    ask.done = [located(1), located(1)]
    tr = Tracker(RANGES, ask, Config(first_check_s=0.0, poll_s=2.0, min_gap_s=0.5))
    run(tr, 0, 2.6)
    assert tr.k == 0 and tr.ahead == 1          # one answer: noted, not acted on
    run(tr, 3.0, 6)
    assert tr.k == 1


def test_low_confidence_answers_are_ignored():
    ask = Scripted()
    ask.done = [located(1, conf=0.3, source="photo")]
    tr = Tracker(RANGES, ask)
    tr.asked_for = True
    run(tr, 0, 3)
    assert tr.k == 0


def test_an_answer_about_a_step_already_left_is_dropped():
    ask = Scripted(latency=3.0)
    ask.done = [located(1, source="photo")]
    tr = Tracker(RANGES, ask)
    tr.asked_for = True
    tr.tick(0.0, None, None, FRAME)             # asks about step 0
    tr.goto(1.0, 2)                             # the novice picks step 3 on the phone
    run(tr, 1.5, 5)
    assert tr.k == 2
    assert not kinds(tr, "step_done")


def test_a_warning_needs_two_positive_mistake_answers_in_a_row():
    ask = Scripted()
    bad = Check(True, 0.9, "corner short of the crease", 1.0, 0.0)
    ok = Check(False, 0.9, "fine", 1.0, 0.0)
    ask.mistake = [bad, ok, bad, bad]
    tr = Tracker(RANGES, ask, Config(mistake_poll_s=2.0, min_gap_s=0.5, first_check_s=100, poll_s=100))
    run(tr, 0, 30)
    warns = kinds(tr, "warn")
    assert len(warns) == 1 and warns[0].data["what"] == "corner short of the crease"


def test_the_throw_step_finishes_when_the_work_leaves_the_view():
    tr = Tracker(RANGES, Scripted(), throw_last=True)
    tr.goto(0.0, 3)
    run(tr, 0.5, 4.0)                            # plane in view, nothing asked
    assert not tr.finished
    tr.lost(4.5)                                 # gone, but not for long enough
    assert not tr.finished
    tr.lost(5.6)
    assert tr.finished and kinds(tr, "complete")


def test_hands_off_on_the_throw_step_is_not_a_throw():
    tr = Tracker(RANGES, Scripted(), throw_last=True)
    tr.goto(0.0, 3)
    tr.asked_for, tr.trigger = True, "settle"
    tr.tick(1.0, None, None, FRAME)
    assert not tr.finished
    tr.asked_for, tr.trigger = True, "tap"
    tr.tick(2.0, None, None, FRAME)
    assert tr.finished
