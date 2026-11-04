# The 3-minute video

Shot list and script for the submission video. Everything shown is real: live runs recorded by
the system itself, with the dashboard screen-recorded while a phone films the person. Sped-up
sections carry a "4x" label; dashboard replays carry a "replay" label.

Target length 2:50. Due Nov 4, 9 PM ET; upload by 5 PM.

The climax is ask-the-expert answered in the expert's own recorded voice, because it works live
every time and nobody else can show it. Mistake catching gets one honest line, not a staged moment.
Pitch the experience gap, not retiring experts (reports/Trades shortage and guidance evidence.md:
trades are not unusually old; the bottleneck is supervised hours and mentors).

## Beats

| Time | What is on screen | Voice-over / sound | Source |
|---|---|---|---|
| 0:00-0:10 | Cold open: the novice at the work, hearing the expert's voice. Caption: "That's Chibuoke's voice. He recorded this an hour ago." (only say "he isn't here" if he is out of the room) | The expert's voice. | Live run |
| 0:10-0:28 | The expert on camera, one line on what new techs get wrong; then recording the job on the glasses, LED visible. Lower third: "The US needs about 207,000 new electricians, HVAC techs, plumbers and mechanics a year (BLS). An apprenticeship is 8,000 supervised hours." | His words, then live sound. | Phone, wide |
| 0:28-0:50 | The compile: `tools/new_procedure.sh` in the terminal, the transcript, then the step list with done-checks and known mistakes; reference photos tile in. Label each model as it runs. | "One recording. Voice Transcribe times the words, Muse Spark writes the steps and the mistakes the expert warned about, SAM picks the photo of each finished step." | Screen recording, sped with a label |
| 0:50-1:00 | Novice puts on the glasses; phone beside the work shows step 1 and the expert's photo. The cloned expert voice gives the first instruction. | "A beginner puts the glasses on and hears the expert." | Phone |
| 1:00-1:35 | Split: dashboard left, phone filming the novice right. Novice takes hands off, the hands-off prompt lights, Spark's answer appears in the log with its reason, the step advances. Two or three steps, 4x between them, 1x on one advance. Lower third: "35 live step advances, none early, none skipped." | "Meta's own researchers say the hard part is deciding when guidance is needed. Here SAM sees the hands leave the work, Muse Spark judges the step, and code decides." | Live run + dashboard |
| 1:35-2:05 | **Ask the expert, 1x, uncut.** Novice holds the button and asks the planted question (e.g. "Do I need to turn anything off before I touch the wires?"). The question appears, then **the expert's own recorded words play** ("EXPERT, RECORDED" on screen, Spark log: "plays the expert at 0:25"), then a one-line spoken answer about what the glasses see. Stopwatch visible. | Natural sound. | Live run (screen-record the Pixel too) |
| 2:05-2:15 | The same guide switched to Hindi: one instruction on screen and spoken. | "Recorded once in English. Guided in Hindi, which Ray-Ban Meta now speaks in India." | Phone |
| 2:15-2:25 | The last step done (the throw, or the thermostat cover on). | Natural sound. | Phone, wide |
| 2:25-2:40 | Report card: time per step against the expert, checks, photos, cost. Then one card: "35 live advances, 0 wrong. Not yet: catching a wrong step on its own (0 of 4 planted mistakes). No published trial yet shows an AI glasses assistant improving novice physical work; this is ours." | "Every number is from real runs, including what it gets wrong." | Screen + card |
| 2:40-2:52 | Builder to camera, one sentence: who this is for. | | Phone |
| 2:52-3:00 | Card: ApprenticeAI. "Glasses that see what we see will become our primary computing devices" (Zuckerberg, 2025). Ray-Ban Meta + Device Access Toolkit, Muse Spark, SAM 3.1, Voice Transcribe, MMS-TTS, DINOv2. Repo link. Chatterbox voice disclosed. | | Card |

## The planted question (ask-the-expert beat)
Spark can only play what the expert actually said. While Chibuoke records, he says one clear "why"
line at the key step (e.g. "Always kill the breaker before you touch the R wire, or you'll blow the
transformer fuse"). After compiling, test it once with
`curl -X POST "localhost:8000/api/ask?q=do%20I%20need%20to%20turn%20anything%20off%20first%3F"`
and check which line it plays. On camera, ask the same question by voice.

## What must be true before filming
- A clean run of the procedure being filmed, completing every step.
- Consent from anyone filmed, and written consent from the expert for the cloned voice. LED visible
  in every shot with the glasses.
- Oct 22 footage is not used anywhere.
- Glasses and phone charged, laptop plugged in, dashboard full screen at 1440x900, QuickTime
  screen recording without the cursor.

## Fallback
If the live HVAC run does not complete, the airplane is the hero: `demo/catch5` completed all
seven steps live, and a new airplane run with the novice filmed covers 1:00-2:25.

## Edit
- 1080p30. Dashboard capture is the main track; phone footage is the inset.
- Captions burned in; model names on screen when a model does something.
- Music under 0:28-1:00 and 2:40-3:00 only, none under the ask.
- Export H.264 1080p, about 20 Mbps, AAC. Watch it on a phone before uploading.

## The X post
The ask beat alone, vertical, 25 s, captioned. One sentence, the repo link, the event hashtag, and
"built with Muse Spark, SAM 3.1 and Voice Transcribe on Ray-Ban Meta".
