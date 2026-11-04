# The 3-minute video

The thermocouple story, recorded Nov 4 at Purdue's Herrick Labs. Everything shown is a real run
recorded by the system (out/sessions/), except where a label says otherwise. Dashboard shots can be
captured from replays at any time: Replay a run, pick the session, Sound on.

Target 2:50. Due Nov 4, 9 PM ET.

## Beats

| Time | On screen | Sound | Source |
|---|---|---|---|
| 0:00-0:10 | Cold open: the beginner at the rack, glasses on. Caption: "That's Chibuoke's voice. He recorded this job once, this morning." | His cloned voice: "Find channel sixteen: the empty slot at the end of the first thermocouple module." | good2 (glasses + your camera) |
| 0:10-0:30 | Chibuoke on camera, one line on what new people get wrong; then him recording the job on the glasses, LED visible. Lower third: "The US needs about 207,000 new electricians, HVAC techs, plumbers and mechanics a year (BLS)." | His words | Interview + expert take 1 (skip 2:49, another person's face) |
| 0:30-0:50 | The compile: `tools/new_procedure.sh` in the terminal, the transcript, the steps appearing. Label each model as it runs. Caption: "One recording. About 30 seconds of Muse Spark." | VO: "Voice Transcribe times his words, Muse Spark writes the steps and the mistakes he warned about." | Screen recording (re-run on a copy if needed) |
| 0:50-1:35 | good2, dashboard left, your camera right. Hands away, the What Spark saw panel fills (NOVICE · NOW beside EXPERT · DONE), "That looks right", next step. Show two steps at 1x, the rest sped up with a "4x" label. Lower third: "11 complete live runs. 57 step advances, every one from the beginner's own pause." | His voice, natural sound | good2 replay + your camera |
| 1:35-2:05 | swap1: silver wire into the left port, hands away, STOP banner, panel shows MISTAKE, his voice stops you; the fix, "That's it. Gold on the left." **Label on screen for the whole beat: "Demonstrated warning (staged)".** | His voice | swap1 replay + your camera |
| 2:05-2:20 | Ask the expert: "Which side does the copper go on?" and his own recorded words play ("EXPERT, RECORDED" on the dashboard). | His recording | novice7 replay (see note below) |
| 2:20-2:30 | Optional: the guide on a simulated Meta Ray-Ban Display with Neural Band gestures. Label "Simulated Display". | Natural sound | Lens app in the emulator (docs/lens.md) |
| 2:30-2:45 | Honest card: "Works: step-by-step guidance in the expert's voice, 11 of 11 full runs completed. Not yet: seeing which wire is in which port from the glasses; the guide says so and sends the beginner to the display." | VO, one sentence | Card |
| 2:45-3:00 | Maker's mark logo. Ray-Ban Meta + Wearables Device Access Toolkit, Muse Spark, SAM 3.1, Voice Transcribe, Omnilingual ASR, MMS-TTS, DINOv2, Spark Open on Baseten. Repo link. Credits: "Voice cloned with Chatterbox, with Chibuoke's written consent. Reference photos re-shot following his procedure." | | Card |

## Does the code match the video?

| Shot | In the repo | Where |
|---|---|---|
| His voice for every line | yes | `procedure.json` expert_voice_only; `Session._say_line`, `_say_step`, `_say_warn` (voice files are local, gitignored) |
| Guided step lines ("take your hands away, so I can see...") | yes | step `show` field, `compile/lines.say_line` |
| Hands away triggers the check | yes | `Session._watch_settle`, signal mode in `guide/state.py` |
| What Spark saw panel | yes | `Session._publish_seen`, `SeenPanel` in `web/app/src/dashboard.jsx` |
| "Got it" on wire steps, never "that looks right" | yes | step `verifiable: false`, `Tracker.no_check` |
| Tip at the constantan step | yes | step `tip`, `_watch_settle` |
| Ask the expert plays his recording | yes | `Session.ask`, `ask_clip_only` / expert_voice_only |
| swap1 STOP | **no, staged** | DEMO_MODE, kept out of the repo by choice; labeled in the video |
| novice7 question | **trigger staged** | the answer path is real (`Session.ask`); the question was fired by a temporary trigger on the pause instead of the phone button. Say "asked through the guide" or re-record with the hold-to-ask button |
| Simulated Display | yes | `android/lens`, `docs/lens.md` |

## Must be true before uploading
- Consent: Chibuoke's signed form covers filming and the cloned voice. LED visible in every glasses shot.
- No other person's face (expert take 1 at 2:49).
- Every staged or simulated moment carries its label for the whole beat.
- Numbers on screen match out/sessions: 11 complete runs, 57 advances, good2 2:32, expert take 2:52, about $0.55 to $0.60 per run.

## Edit
- 1080p30. Dashboard capture at 1440x900 or larger (smaller windows overlap the panels).
- Captions burned in; model names on screen when a model does something.
- Music only under 0:30-0:50 and 2:45-3:00, never under his voice.
- Export H.264 1080p, about 20 Mbps, AAC. Watch it on a phone before uploading.

## The X post
The swap beat or the ask beat alone, vertical, 25 s, captioned, with the same labels. One sentence, the
repo link, the event hashtag, and "built with Muse Spark, SAM 3.1 and Voice Transcribe on Ray-Ban Meta".
