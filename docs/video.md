# The 3-minute video

Shot list and script for the submission video. Everything shown is real: live runs recorded by
the system itself, with the dashboard screen-recorded while a phone films the person. Sped-up
sections carry a "4x" label; dashboard replays carry a "replay" label.

Target length 2:50. Due Nov 4, 9 PM ET; upload by 5 PM.

The climax is ask-the-expert, because it works live every time. Mistake catching gets one honest
slide, not a staged moment.

## Beats

| Time | What is on screen | Voice-over / sound | Source |
|---|---|---|---|
| 0:00-0:10 | Black. One line: "Every expert retires with what no manual records." Cut to the glasses on the bench. | Silence, then one tone. | Title card |
| 0:10-0:28 | The expert (Chibuoke on the thermostat if the HVAC run lands; otherwise the airplane expert run) working and narrating. Glasses LED visible. | Live sound. | Phone, wide |
| 0:28-0:50 | The compile: `tools/new_procedure.sh` in the terminal, the transcript, then the step list with done-checks and known mistakes; reference photos tile in. Label each model as it runs. | "One recording. Voice Transcribe times the words, Muse Spark writes the steps and the mistakes the expert warned about, SAM picks the photo of each finished step." | Screen recording, sped with a label |
| 0:50-1:00 | Novice puts on the glasses; phone beside the work shows step 1 and the expert's photo. The cloned expert voice gives the first instruction. | "A beginner puts the glasses on and hears the expert." | Phone |
| 1:00-1:35 | Split: dashboard left, phone filming the novice right. Novice takes hands off, the hands-off chip lights, Spark's answer appears with its reason, the step advances. Two or three steps, 4x between them, 1x on one advance. | "When the hands come off the work, SAM sees it, Muse Spark compares the photo with the expert's, and the guide moves on." | Live run + dashboard |
| 1:35-2:05 | **Ask the expert, 1x, uncut.** Novice holds the button: "which way does this fold go?" The transcript appears, then the answer is spoken, grounded in the current photo. Clock visible. | Natural sound. | Live run |
| 2:05-2:15 | The same guide switched to Hindi: one instruction on screen and spoken. | "Recorded once in English. Guided in any language." | Phone |
| 2:15-2:25 | The last step done (the throw, or the thermostat cover on). | Natural sound. | Phone, wide |
| 2:25-2:40 | Report card: time per step against the expert, checks, photos, cost. Then one slide: "What it does not do yet: catching a wrong fold automatically. 0 of 4 planted mistakes caught. Next: measuring the work's outline from SAM's mask." | "Every number is from this run, including what it gets wrong." | Screen + slide |
| 2:40-2:52 | Builder to camera, one sentence: who this is for. | | Phone |
| 2:52-3:00 | Card: ApprenticeAI. Meta models used, listed. Repo link. Chatterbox voice disclosed. | | Card |

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
