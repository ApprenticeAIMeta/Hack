# Submission

ApprenticeAI for the Meta Global AI Developer Hackathon (Indie track, Push the Frontier). Every
number below is from a recorded run in out/sessions or a measurement in docs/.

## One line
Experts can't stand next to every beginner. Their knowledge can: one recording of a skilled job
becomes a live, hands-free guide in the expert's own voice.

## Problem
The US needs about 207,000 new electricians, HVAC technicians, plumbers and industrial mechanics a
year (BLS), mostly to replace people who leave. Competence is gated by supervised time: an
electrician apprenticeship is 8,000 hours next to someone who already knows, state law caps how many
apprentices one journeyman can supervise, and only about one construction apprentice in four
finishes. What an expert knows lives in their hands and habits: the order of the work, what "done"
looks like, the mistakes they have learned to avoid. Shadowing passes it on one person at a time.

## What it does
1. **Capture.** The expert does the job once on Ray-Ban Meta glasses and talks through it.
2. **Compile.** Muse Voice Transcribe times his words; Muse Spark reads the video with the timed
   transcript and writes the procedure under a strict schema: each step's instruction, a spoken
   line for the beginner, what "done" looks like, why it matters in his words, and the mistakes he
   warned about, kept apart from the ones the model adds. Every line is rendered in his cloned voice.
3. **Guide.** A beginner wears the glasses and hears the expert, step by step. Each line ends with
   how to show the finished step ("take your hands away and look at the connector, so I can check
   it"). SAM 3.1 sees the hands leave the work; Muse Spark checks the frame against the expert's
   photo of that step and the guide moves on, or warns and holds the step.
4. **Ask.** The beginner asks a question; Muse Spark finds the moment of the expert's own recording
   that answers it, and his actual words play.
5. **See the reasoning.** The dashboard shows, at every check, the beginner's frame beside the
   expert's done photo with the verdict.

## The test: thermocouple wiring at Purdue's Herrick Labs
Chibuoke recorded wiring a type T thermocouple into channel 16 of a National Instruments module (two
takes, 2:52 each). The compile produced six steps in about 30 s of Muse Spark.

- **11 complete guided runs**, **57 step advances**, every one triggered by the beginner's own
  hands-off pause, **0 manual overrides**.
- Best run: **2:32** for all six steps, against the expert's 2:52.
- Signal to next instruction: about 3 s on steps the camera cannot verify, 7 to 9 s with a Muse
  Spark check.
- Cost: $0.55 to $0.61 per three-minute guided run.
- Earlier procedure, a paper airplane: 35 live advances, none early or skipped; run catch5
  completed all seven steps including the throw.

## What does not work yet (stated plainly)
From a head-mounted camera, which wire sits in which port of this connector is not visible, even in
12 MP stills with a Muse Spark-located, upscaled crop of the connector; tested on correct and
mirrored photos. So the guide never claims to verify those steps: it acknowledges them, plays the
expert's warning at the critical step, and sends the beginner to the real check, the temperature
display. Automatic mistake catching is not reliable on either procedure. The mistake stop in the
demo video is labeled as a demonstration.

## How it uses Meta's models
- **Muse Spark 1.3:** compiles the procedure; checks the beginner's frame against the expert's
  reference at each signal (a quarter-turned confirmation runs in parallel so false alarms cost one
  call's latency); picks which of the expert's recorded turns answers a question; translates the
  guide; finds targets SAM cannot name.
- **SAM 3.1:** the hands-off sensor that decides when to look, and workpiece tracking.
- **Muse Voice Transcribe:** turns the expert's narration into timed turns that are exact cut points
  for his voice; transcribes the beginner's questions.
- **Ray-Ban Meta + Wearables Device Access Toolkit:** the 720p 15 fps camera stream and the speakers;
  a simulated Ray-Ban Display mode with Neural Band input built on the toolkit's Display API.
- **Open weights:** Omnilingual ASR (local questions in any language), MMS-TTS (Hindi and Spanish
  guides), DINOv2 (progress gauge). **Muse Spark Open on Baseten** takes over if the Meta Model API
  cannot answer.

## How it was built
Android (Kotlin, Compose, DAT 1.0.1) streams HEVC from the glasses to a FastAPI server over a
WebSocket; the server decodes with PyAV, runs SAM, the progress model and a step tracker, and asks
Muse Spark only when the beginner signals. Dashboard and phone guide are React, Tailwind and Framer
Motion. Every session is recorded and replays on the dashboard with every model call and its reason.

## Challenges
- A head-worn camera never holds still, so "done" is the hands leaving the work, not the picture
  settling; signals are ignored while the guide is speaking.
- On a crowded wiring rack, step answers from a single frame flipped; the fix was letting the
  beginner's pause decide when, and Spark only judge for mistakes.
- The glasses' microphone ends the camera stream, so questions go through the phone.
- An expert demonstrating a mistake compiled as steps ("connect reversed wires"); the compile now
  folds demonstrated mistakes into warnings.

## What's next
A guided capture protocol so the expert's recording always contains clear evidence of each finished
step; continuous target tracking (built, off by default); a specific compiled check per step; a real
Ray-Ban Display build.

## Built during the hackathon
Everything was written from Oct 27, 2026, by one person with Claude Code. See
BUILT_DURING_HACKATHON.md for provenance.

## Disclosures
- The expert's voice is cloned locally with Chatterbox (open source, MIT) from 30 s of his own
  narration, with his written consent. The rendered lines stay on the laptop (gitignored), except
  the airplane demo's, which ship for the no-key replay.
- Reference photos for the thermocouple steps were re-shot hands-free following his procedure; his
  own frames are kept alongside them (local, with the rest of the compiled media).
