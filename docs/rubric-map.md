# Where to find each judging criterion

Weights are from the participant guide.

| Criterion | Evidence |
|---|---|
| Impact (30%) | Expert knowledge leaves with retiring trades workers. One narrated recording of one expert becomes a guide anyone can follow, in their own language, with no written manual. |
| Meta AI tooling (25%) | Five Meta models with separate jobs, below. Beyond prompting: SAM 3.1 is used as a sensor (is a hand on the work right now?) that decides when to judge; Muse Spark answers spoken questions as the expert, grounded in the expert's transcript and a fresh photo from the glasses; the progress model is trained on expert footage degraded to match the glasses' Bluetooth stream. Open weights: MMS-TTS and DINOv2 run locally; a compile on Muse Spark Open is measured in [open-weights.md](open-weights.md). |
| Demo (25%) | `/?demo=1` replays `demo/catch5` from a fresh clone with no key or glasses. Each model's latency and every Spark answer with its reason are on screen; runs are labeled live or replay. |
| Technical depth (20%) | Stream simulation for training ([progress-notes.md](progress-notes.md)); a step tracker whose rules each trace to a measured failure in a live run ([eval.md](eval.md), git history); per-call timeouts, a spend cap, session recording and replay. |

## What each model does

| Model | Job | Code |
|---|---|---|
| Muse Spark 1.3 | Compiles video and transcript into steps; judges which step the novice has reached; answers spoken questions as the expert; translates the guide | `compile/steps.py`, `guide/policy.py`, `guide/session.py` (ask), `compile/localize.py` |
| SAM 3.1 | Tracks the workpiece in each frame and photo; detects hands on the work (hands-off signal); picks hands-off reference photos | `guide/session.py`, `progress/train.py` |
| Muse Voice Transcribe | Times the expert's speech for the compile; transcribes the novice's questions | `compile/transcribe.py`, `server.py` (`/api/ask`) |
| MMS-TTS (open weights) | Speaks instructions, warnings and answers in the novice's language | `models/speech.py` |
| DINOv2 (open weights) | Embeds workpiece crops for the progress model and the "expert at the same point" mirror | `progress/embed.py` |

Not Meta: the optional cloned expert voice (Chatterbox, open source, MIT), disclosed in the README.

Experimental and not wired in: `guide/shape.py` (silhouette checks from SAM masks) and
`policy.verify` (strict end-of-step check); see eval.md for why.
