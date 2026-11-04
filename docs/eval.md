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
