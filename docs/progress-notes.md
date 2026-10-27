# Progress model notes (Day 3, Oct 27)

Airplane procedure, 2 expert runs (expert1 122 s, expert2 137 s), novice = 7 fps glasses stream (115 s).
Frames at 2 fps; SAM 3.1 workpiece crop (track that touches a tracked hand most); DINOv2-small
(CLS + mean patch, 768-d) on Apple MPS at ~21 ms/frame.

Labels are self-supervised: normalized time within each expert's task span. Metric: Spearman rank
correlation between predicted progress and elapsed time (novice task = first 100 s).

| Training data | Head | Expert->expert (held out) | Novice (raw / causal median 5) |
|---|---|---|---|
| clean expert crops | ridge | 0.82 / 0.88 | 0.34 / 0.42 |
| clean expert crops | kNN-5 | 0.58 / 0.75 | 0.48 / 0.53 |
| stream-simulated crops | ridge | 0.82 / 0.89 (stream test) | 0.75 / 0.86 |
| stream-simulated crops | kNN-15 | | 0.75 / 0.89 |
| clean + stream | ridge + kNN-15 mean (shipped) | | **0.79 / 0.93** |

Findings
- The domain gap is the channel, not the person: expert footage is native 1536x2048, the novice
  arrives through a ~55 kbps Bluetooth stream. Re-encoding expert footage through the same
  channel (7 fps, 55 kbps HEVC) before embedding moved novice correlation from 0.42 to 0.86+.
- Workpiece selection by size locks onto static distractors (the notebook). Selecting the track
  that touches the hands fixed expert2 (99% frames tracked, was the wrong object).
- Predictions are compressed toward the middle (novice spans ~0.35-0.8). Order is right; the
  mirror runs about one step ahead mid-task. The guide state machine (forward-only steps,
  Spark check to confirm) is expected to absorb this.
- Not yet tested: a run with a real mistake (needs a recording). Two signals are available:
  progress drop, and nearest-expert similarity drop (states the expert never showed).
