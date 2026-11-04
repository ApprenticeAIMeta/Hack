# The 3-minute video

Shot list and script for the submission video. Everything shown is real: a live run, recorded
by the system itself, with the dashboard screen-recorded while a phone films the person. Any
section that is sped up carries a "4x" label on screen. The catch segment is one uncut take with
the clock visible.

Target length 2:50. Due Nov 4, 9 PM ET; upload by 5 PM.

## Beats

| Time | What is on screen | Voice-over / sound | Source |
|---|---|---|---|
| 0:00-0:12 | Black. One line of white text: "Every expert retires with what no manual records." Cut to the glasses on the desk. | Silence, then one quiet tone. | Title card, phone shot |
| 0:12-0:30 | Devansh wearing the glasses, folding, narrating as the expert: "...corners meet right on the center line, or the nose comes out crooked." The glasses' LED is visible. | Live sound. | Phone, wide, from the expert recording session (re-film 20 s of it tomorrow) |
| 0:30-0:55 | The compile. Terminal: `build ... --name airplane`. Transcript lines appear, then the step list with titles, done-checks and known mistakes. Label each as it appears: "Muse Voice Transcribe", "Muse Spark". Then the six reference photos tile in: "SAM 3.1 crops the work". | "One recording. Transcribe times the words. Spark turns the video and the words into six steps, what done looks like, and the mistakes the expert warned about." | Screen recording of a real compile (cached, 70 s, sped to 25 s with a 3x label) |
| 0:55-1:05 | The phone propped on the desk, the step 1 screen; the glasses on a second person (the novice). | "A beginner puts the glasses on. The phone shows what the expert's work looked like at the end of each step. The expert's voice says what to do." | Phone shot |
| 1:05-1:40 | Split: dashboard screen recording (left), phone filming the novice (right). Steps 1-3 go by. Green check flashes, Spark log rows appear, the progress ring moves. Sped to 4x with a label; the clock on the dashboard stays visible. | "SAM tracks the sheet. DINOv2 scores progress every second. Muse Spark checks a still from the glasses: which step is this, and is anything wrong?" | Dashboard capture + phone, from the `demo1` run |
| 1:40-2:15 | **The catch. One uncut take, 1x.** Novice folds a corner short of the crease, hands off. Clock visible. The dashboard's edges pulse amber, "STOP" slides in, the phone turns amber: "corners not meeting at center". The expert's voice. Novice unfolds, fixes it, taps Got it, the step advances. | Natural sound, no music. | Dashboard capture + phone, same run |
| 2:15-2:30 | The novice throws the plane. It flies. | Natural sound. | Phone, wide |
| 2:30-2:45 | The report card on the dashboard: time per step vs the expert, checks, stills, warnings, cost. Then one slide of real numbers from docs/eval.md, including the misses. | "Every number here is from this run. Here is what it gets wrong today." | Screen + slide |
| 2:45-2:55 | Devansh to camera, one sentence: what this is for. | | Phone |
| 2:55-3:00 | Card: ApprenticeAI. "Recorded once." Meta models used, listed. Repo link. | | Card |

## What must be true before filming
- The `demo1` run completes and the catch fires on the planted mistake. If the catch does not
  fire reliably, the 1:40 beat becomes: novice taps "Check my step", the phone answers "Not yet:
  corners don't reach the crease", novice fixes it. Say on screen that the check was asked for.
- The novice is someone other than the expert if at all possible (a friend, 10 minutes). If not,
  say so on screen.
- Glasses charged; phone at 100%; laptop plugged in; dashboard full screen at 1440x900 for a
  clean capture (QuickTime screen recording, no cursor).
- Consent from anyone filmed; LED visible on the glasses in every shot that has them.

## Filming order (about 90 minutes)
1. Dashboard screen recording running, phone on a tripod: the full guided run with the planted
   mistake at step 3 only. Two takes.
2. The throw, three takes.
3. Twenty seconds of the expert narrating a fold (the 0:12 beat).
4. The to-camera sentence, three takes.
5. Phone close-ups: the step screen, the Stop screen, the report card.

## Edit
- Timeline 1080p30. The dashboard capture is the main track; the phone footage is the inset.
- Captions burned in. Model names on screen when a model does something.
- Labels: "4x" during sped-up sections; "replay" if any shot is a dashboard replay rather than
  the live run.
- Music under 0:30-1:40 and 2:30-3:00 only, cut dead at 1:40.
- Export H.264 1080p, about 20 Mbps, AAC. Check on a phone before uploading.

## The X post (Most Viral award)
Video: the catch beat alone, vertical, 25 s, with captions. Text: one sentence, the repo link,
the hashtag from Discord, and "built with Muse Spark, SAM 3.1, Voice Transcribe on Ray-Ban Meta".
