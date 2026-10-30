# Where to find each judging criterion

Weights are from the participant guide. Items marked planned are not built yet.

| Criterion | Evidence |
|---|---|
| Impact (30%) | Expert knowledge leaves with retiring trades workers. The guide works from one recording of one expert, with no written manual. |
| Meta AI tooling (25%) | Five models with separate jobs, below. Spark checks are sent a still taken for the check, with a cached prefix of reference images. Planned: an open-weight checker. |
| Demo (25%) | `/?demo=1` replays a recorded run with no glasses. Each model's latency is shown on screen, and runs are labeled live or replay. |
| Technical depth (20%) | Training simulates the Bluetooth stream ([progress-notes.md](progress-notes.md)). Spark's answers must agree or be very confident before a step moves ([guide-notes.md](guide-notes.md)). Measured results are in [eval.md](eval.md) and [api-notes.md](api-notes.md). |

## What each model does

| Model | Job | Code |
|---|---|---|
| Muse Spark 1.3 | Compiles video and transcript into steps; checks progress and mistakes live | `compile/steps.py`, `guide/policy.py` |
| SAM 3.1 | Finds and tracks the workpiece in each frame and still | `guide/session.py` |
| Muse Voice Transcribe | Times the expert's speech so real voice clips can be cut | `compile/transcribe.py` |
| DINOv2 | Embeds workpiece crops for the progress model | `progress/embed.py` |
