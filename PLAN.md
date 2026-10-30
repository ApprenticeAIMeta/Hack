# ApprenticeAI build plan

Meta Global AI Developer Hackathon, Indie track, problem statement: Push the Frontier.
Building period Oct 27 to Nov 4 2026. Submission due Nov 4, 9:00 PM ET (target: submitted by 5:00 PM).

## The product in one line

Record an expert doing a physical job once through Meta AI glasses; a novice wearing the same glasses
then gets that expert's guidance, in the expert's own voice, at the moment it matters.

## Judging rubric and what answers it

| Criterion | Weight | Answered by |
|---|---|---|
| Impact | 30% | Real HVAC technician, real novices, trades workforce numbers |
| Use of Meta AI tooling | 25% | Five Meta models, each doing a distinct, non-obvious job (below) |
| Demo | 25% | One hero beat (manual diff setup, catch climax); live airplane demo for finals |
| Technical depth | 20% | Learned progress model, escalation design, eval table |

## The demo: one hero beat

Judges remember one moment, so the video builds to a single climax instead of three equal moments.

- **Setup: manual diff.** Muse Spark reads the service manual PDF and the expert's session and shows
  where the expert departs from the manual, and why. This establishes that the system holds knowledge
  the manual does not.
- **Climax: the catch.** The novice makes a mistake nobody listed. On the live mirror (novice view next
  to the expert's frame at the same progress) the novice's progress curve drops, the phone turns into
  a full-screen warning beside the expert's picture, and the expert's recorded voice plays with it.
  The visual always carries the guidance; audio complements it and is never the only channel.
- The climax must be reliable on camera: single-frame Spark checks get fooled by viewpoint
  (docs/api-notes.md), so the catch is driven by the progress model plus a reference-frame check, and
  the mistake is rehearsed and verified in replay before filming.

### 3-minute video structure

| Time | Beat |
|---|---|
| 0:00-0:55 | The HVAC technician: who they are, what leaves when they retire, what a new tech gets wrong. Real footage of a real technician. |
| 0:55-1:30 | Capture and compile: expert session becomes a procedure; manual diff appears (setup) |
| 1:30-2:25 | Novice run, live mirror, the mistake, curve drops, expert's voice warns (climax) |
| 2:25-2:45 | Eval table on screen for about 5 seconds; per-model cost and latency |
| 2:45-3:00 | Close: glasses as the keyboard for physical work |

Every model gets a small on-screen caption the moment it acts (Muse Voice Transcribe, Muse Spark,
SAM 3.1, Muse Glimmer, DINOv2), so "five models, five distinct jobs" is visible, not claimed.

### Video vs finals

The video is the controlled demo: film from replay mode wherever live is not needed, and capture the
climax take as many times as it takes. Finals are the risky live run: the airplane demo live on the
glasses, with a pre-recorded airplane run cued up as the backup.

## Meta models and their jobs

| Model | Job |
|---|---|
| Muse Voice Transcribe | Timestamped transcript of expert narration (drives step boundaries and voice clips) |
| Muse Spark | Compile video + transcript into procedure.json; manual diff (video + PDF); explain detected mistakes; translation |
| SAM 3.1 | Segment and track workpiece and hands; crops feed the progress model; masks for the video |
| Muse Glimmer / Spark Open (Modal or Baseten) | Cheap live per-tick checks ("is step k done?"), escalate to Spark when unsure |
| Meta open-weights vision encoder (DINOv2) | Frame embeddings for the learned progress model |

Building is done with Claude Code; only Meta models run inside the product.

## Architecture

```
COMPILE (offline, per expert session)
  native clip -> audio -> Muse Voice Transcribe (timestamps)
              -> frame gate -> keyframes -> SAM 3.1 workpiece/hand crops
  Muse Spark(video|keyframes + transcript) -> steps: instruction, why, t_start/t_end,
      objects, done_check, known_mistakes, reference keyframe, voice clip
  Muse Spark(session + manual PDF) -> manual diff
  progress model: embeddings(crops) -> regressor trained on t/T across expert runs
  => procedure/<name>/procedure.json + clips/ + keyframes/ + progress.pt

GUIDE (live or replay)
  frames (glasses 7 fps via phone, or replay file) -> frame gate (~1-2 fps)
  -> progress model (every kept frame, local, ms)
  -> state machine over steps:
       progress past step end + Glimmer "done?" check -> advance, play next instruction clip
       progress drop or stall, or Glimmer "mistake?" -> Spark explains -> play warning
  -> events stream to phone (audio to glasses) and to web dashboard (mirror, curves, masks)
```

## Repo layout

```
backend/
  apprentice/
    models/      meta.py (Model API client), open.py (Glimmer / Spark Open endpoint)
    capture/     frame_gate.py, media.py (audio extract, keyframes)
    compile/     transcribe.py, steps.py, manual_diff.py, clips.py, build.py
    progress/    embed.py, crop.py, train.py, align.py
    guide/       state.py, policy.py, session.py
    server.py    FastAPI + WebSocket (phone and dashboard clients)
    replay.py    run guide on a recorded clip, write a timeline
  eval/          labels/, run_eval.py
web/             dashboard: procedure cards, manual diff, mirror + curves
android/         phone app: glasses stream -> backend, audio out, voice, photo
procedures/      compiled procedures (small artifacts only)
```

## Schedule

| Day | Date | Deliverable | Done when |
|---|---|---|---|
| 1 | Tue Oct 27 | Repo; API spike; frame gate | Latency + quality numbers for Transcribe, Spark (image, video), SAM 3.1 in `docs/api-notes.md` |
| 2 | Wed Oct 28 | Compile v1 on airplane | `procedure.json` with steps, why, times, keyframes, voice clips from expert1+expert2 |
| 3 | Thu Oct 29 | Progress model v1 + mirror alignment | Novice clip progress rank correlation measured; decision: >0.8 adopt as tracker |
| 4 | Fri Oct 30 | Guide loop in replay + eval harness | Replay of novice clip advances steps; mistake clip triggers a warning; eval table generated |
| 5 | Sat Oct 31 | Hosted backend + Android client + dashboard v1 | Phone (MockDeviceKit feed) -> hosted backend -> audio plays; dashboard shows mirror + curves |
| 6 | Sun Nov 1 | HVAC session | Expert + 2 novices recorded; furnace procedure compiled; manual diff produced; X clip cut |
| 7 | Mon Nov 2 | Live integration + X post | End-to-end live on glasses; latency tuned; open-weights path working; X clip posted (traction can show in the submission; Most Viral counts engagement to Nov 8) |
| 8 | Tue Nov 3 | Film + write | Climax take captured from replay; model captions; eval table final; airplane backup run recorded for finals; README + SUBMISSION.md drafted |
| 9 | Wed Nov 4 | Ship | Video edited and uploaded (unlisted); secret scan; repo public; submitted by 5 PM |

## Cut lines (in order, if behind)

1. Translation to a second language
2. Open-weights bonus path (keep Spark-only checks)
3. Live mirror becomes a post-hoc replay visualization instead of live
4. Progress model becomes a chart only; Spark/Glimmer checks drive the tracker
Never cut: real HVAC expert footage, the catch climax (curve drop + expert's voice), a working live airplane demo.

## Open dependencies (owner: Devansh)

- HVAC technician or instructor session, target Oct 31 to Nov 1 (ASHRAE, Ivy Tech outreach)
- Furnace model number and its service manual PDF
- Glasses: on hand for the whole hackathon and finals (daily live testing)
- Airplane mistake run (phone camera is fine; compile does not care which camera)
- Modal and Baseten credit requests

## Budget

$150 Meta Model API credits. Log cost per call type in `docs/api-notes.md` on day 1;
compile runs are the expensive path, live ticks go to the open-weights endpoint where possible.

## Rules to respect

- New work only: everything in this repo is built from Oct 27. Pre-hackathon setup (glasses SDK
  bring-up, sample-app tuning, test footage) is disclosed in the README and kept out of the repo.
- Repo must be public at submission; no secrets in history.
- Never frame the product as a tutor or education chatbot (listed anti-project).
