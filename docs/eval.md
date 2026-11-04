# Evaluation

Every live run with the glasses, Nov 3, 2026, paper airplane, the builder as novice. Numbers are
computed from each run's `ticks.jsonl` (the runs are in `out/sessions/`; catch5 is bundled in
`demo/`). The procedure had six steps until catch5, which added throwing the plane as step 7.

| Run | Length | Steps done | Completed | "Which step" checks | Spark call latency (median) | Asked to answered (median) | Hands-off checks | Planted mistakes | Caught | Positive mistake answers | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| guided1 | 163 s | 6 of 6 | yes | 22 | 3.5 s | 5.1 s | (not built) | none | - | 0 | $0.69 |
| catch1 | 225 s | 5 of 6 | no | 16 | 4.0 s | 10.3 s | (not built) | 3: corners short (s3), points inside (s5), one wing (s6) | 0 | 1 | $0.84 |
| catch2 | 342 s | 6 of 6 | yes | 22 | 5.1 s | 10.7 s | 2 | not recorded | 0 | 4 | $1.29 |
| catch3 | 203 s | 6 of 6 | yes | 17 | 4.0 s | 10.9 s | 5 | 1: one wing (s6) | 0 | 1 | $0.83 |
| catch4 | 181 s | 5 of 6 | no | 17 | 3.8 s | 10.0 s | 4 | not recorded | 0 | 4 | $0.74 |
| catch5 | 271 s | 7 of 7 | yes | 18 | 3.8 s | 11.1 s | 5 | not recorded | 0 | 2 | $1.00 |

"Spark call latency" is the model call alone. "Asked to answered" is from the moment a check is
asked to the moment its answer reaches the tracker, which includes taking a still from the glasses
(the phone waited for the spoken line to finish, then 2 to 3 s of capture). After catch5, timed
checks were moved to the 720p stream frame and the phone's wait capped at 2.5 s; replaying 45 s of
catch5's footage through the server after that change, timed checks answered in a median 6.1 s.

## What these runs show
- Step tracking works: four of six runs completed, the two that did not stopped at the last step.
  Each fix between runs is in the git history with the run that motivated it.
- Hands-off advancing works once the detector looked for hands over the paper rather than for a
  still picture (the camera is on a moving head; catch2 fired twice in five minutes before that).
- Mistake catching does not work yet. No planted mistake produced a warning. A warning needs two
  positive answers in a row, and the positive answers that did occur were not the planted ones.
  Example, catch5 at 51 and 55 s: "folded crosswise instead of lengthwise", conf 0.85 and 0.9; the
  photo shows a correct lengthwise fold with the sheet lying sideways relative to the expert's, so
  this was a false positive caused by orientation. The two-in-a-row rule kept it from warning.
- Offline tests on these runs' stills: a stricter end-of-step question caught 0 of 3 planted
  mistakes; direct yes/no questions caught 1 of 3 and flagged 3 of 3 correct folds; the same
  questions on SAM-mask cutouts caught 1 of 3 and flagged 2 of 3 correct folds. Both the stills and
  the expert's reference photos had hands on the paper, which is why the hands-off photo became the
  moment a step is judged.

## Earlier, replayed from recordings (Oct 27)
Before the glasses streamed at 720p, the tracker was tuned on a 7 fps 504x896 novice recording
replayed through the server: median step timing error 7.1 s (mean 9.4 s) over five steps, 46 Spark
checks, $0.20, 0 false warnings, no planted mistakes. See progress-notes.md, stills-test.md,
confirm-test.md, progress-gate.md and open-weights.md for the measurements behind each design
choice, and api-notes.md for the day-1 model measurements.

## The task progress gauge
The DINOv2 progress model alone read outside the current step's share of the task on 86 of 136
frames of catch5 (64% through step 3, where the expert was 45-53%; 24-41% on the throw). The
gauge is now anchored to the step tracker (`progress/fuse.py`): the step sets the range, DINOv2
places the work within it, capped at 90% until the step is confirmed and at twice the expert's
pace, never moving backwards. On catch5 it stays inside each step's range on every frame, with
0 backward moves, and the expert mirror always shows the current step. Older recorded runs are
re-gauged on replay.

## Were the step advances right?
Every advance in the six live runs, checked against the still from the glasses that it was judged
on (contact sheets built from each run's `photos/` and `ticks.jsonl`; labelled by eye, one rater).
"Done" means the still shows that step's result; "can't tell" means hands cover the fold or the
fold is under way; "wrong" would mean the still shows an earlier step.

| Run | Advances | Judged on a still | Done | Can't tell | Wrong | Step picker used to go back |
|---|---|---|---|---|---|---|
| guided1 | 6 | 3 | 1 | 2 | 0 | (not built yet) |
| catch1 | 5 | 4 | 3 | 1 | 0 | (not built yet) |
| catch2 | 6 | 6 | 5 | 1 | 0 | 0 |
| catch3 | 6 | 6 | 4 | 2 | 0 | 0 |
| catch4 | 5 | 5 | 3 | 2 | 0 | 0 |
| catch5 | 7 | 6 | 6 | 0 | 0 | 0 |
| **Total** | **35** | **30** | **22** | **8** | **0** | **0 of 24** |

- No advance was onto a step the still showed as not started, and no step was ever skipped (the
  tracker advances one step at a time by design).
- In the four runs with the step picker (catch2 to catch5), the novice never used it to undo an
  advance: 0 corrections in 24 advances.
- The "can't tell" stills are the reason hands-off became the trigger: the stills with hands on
  the paper are the ones a person cannot judge either.
- catch1's planted mistakes (corners short, points inside, one wing) were advanced past: the
  step was attempted but done wrong, which is the mistake-catching gap above, not a tracking error.
- The other 5 advances (4 judged on a stream frame, plus the throw, which ends when the plane leaves the view) have no still to check.

## Mistake checks: two things tried on Nov 4
**Silhouette measures (SAM masks), not adopted.** On the step-end stills of the six runs, the
outline's mirror symmetry and its overlap with the expert's outline did not separate the planted
mistakes from correct work: on step 3, the planted "corners short" scored 0.775 symmetry, inside
the 0.27 to 0.92 range of correct folds. Two causes: SAM leaves hands out of the paper mask, so a
hand on the sheet bites holes in the outline (the expert's own step 3 reference scores 0.48), and
after the fold in half the correct top-down outline is a triangle, so symmetry is the wrong test.
It needs hands-off stills and hands-off reference photos to be worth retrying.

**A positive mistake answer must survive a quarter turn, adopted.** When Spark says "mistake",
the same picture is asked again turned 90 degrees, and the warning is kept only if Spark says so
again (`session.confirm_turned`; one extra call, on positive answers only). The two "folded
crosswise" answers on correct lengthwise strips lying sideways (catch4 at 1:05, catch5 at 0:51)
are dropped; on the six photos that drew positive mistake answers live, positives fall from 2 to
0. It cannot add catches: the planted mistakes (catch1 steps 3 and 5, catch3 step 6) still get no
positive answer to confirm.
