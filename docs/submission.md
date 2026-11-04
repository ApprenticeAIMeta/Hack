# Submission draft

Draft answers for the Cerebral Valley submission form (Meta Global AI Developer Hackathon, Indie
track). Edit against the real questions before submitting; every number below is from a recorded
run in out/sessions or a measurement in docs/.

## Project name
ApprenticeAI

## One line
An expert records a job once through Meta glasses; anyone wearing the glasses after them is guided
through it step by step, in the expert's voice, in their own language.

## Problem
Skilled trades lose knowledge every time an expert retires or changes jobs. What an experienced
technician knows is in their hands and their habits, not in a manual: the order they do things in,
what "done" looks like, the mistakes they have learned to avoid. Shadowing transfers it, but an
expert can only stand beside one apprentice at a time.

## What it does
1. Capture. The expert does the job wearing Ray-Ban Meta glasses and talks through it, the way they
   would explain it to someone new.
2. Compile. Muse Voice Transcribe times the expert's words; Muse Spark watches the video with that
   transcript and writes the procedure: each step's instruction, what it looks like when done, why
   it matters (only when the expert said so), and the mistakes the expert warned about. SAM 3.1
   picks the photo of each finished step with the hands off the work.
3. Guide. A novice puts on the glasses. The phone propped beside the work shows the step and what
   the expert's result looked like; a voice says what to do. When the novice sets the work down and
   takes their hands off, the glasses take a photo and Muse Spark judges it against the expert's;
   the guide moves on, or says what is not done yet.
4. Ask. The novice holds a button and asks anything ("which way should the crease go?"). Voice
   Transcribe hears it, Muse Spark answers as the expert from the recording and from what the
   glasses see right now, and the answer is spoken.
5. Any language. Muse Spark translates the guide into the novice's language and Meta's MMS speaks
   it; the expert recorded once, in English.
6. Report. At the end, time per step against the expert, checks, photos and cost for the run.

## How it uses Meta's models
- Muse Spark 1.3: compiles video plus transcript into a procedure; live, judges which step the
  novice has reached from stream frames and photos (cached reference prefix, about 4 to 6 s an
  answer); answers the novice's questions as the expert; translates the guide.
- SAM 3.1: tracks the workpiece in every stream frame (median under 1 s), finds the work in each
  full-resolution photo from a one-call box prompt, detects whether a hand is on the work (the
  "hands off" signal), and picks the expert's reference photos with the hands off.
- Muse Voice Transcribe: times the expert's narration for the compile; transcribes the novice's
  spoken questions (about 1.3 s).
- Open weights: MMS-TTS (Meta) speaks instructions, warnings and answers in 1,100+ languages;
  DINOv2 (Meta) embeds workpiece crops for the progress model.
- Ray-Ban Meta glasses through the Wearables Device Access Toolkit: 720p 15 fps stream and
  full-resolution photos on request.

## How it was built
Android app (Kotlin, Jetpack Compose) streams HEVC from the glasses to a FastAPI server over a
WebSocket; the server decodes with PyAV, tracks with SAM, scores progress with DINOv2, and a step
tracker decides when to ask Muse Spark. The phone guide and the live dashboard are React, Tailwind
and Framer Motion. Every session is recorded and can be replayed on the dashboard with every model
call and its reason.

## Measured
- Six live runs on Nov 3 (docs/eval.md). catch5 guided all seven steps of the paper airplane end to
  end, including the throw, finishing at 182 s of a 271 s session.
- A Muse Spark step check takes 3.5 to 5 s (median per run). In the live runs a check took 10 to
  11 s from being asked to being answered, mostly waiting for a still from the glasses; after
  moving timed checks to the stream frame, the same footage replayed answered in a median 6 s.
- Voice Transcribe heard a spoken question word for word in 1.1 to 1.4 s; the spoken answer arrives
  about 7 s after the button is released.
- Hindi: 19 lines translated and voiced in about a minute.
- Cost: $0.69 to $1.29 per guided run.

## What does not work yet (stated plainly)
Catching a mistake automatically is not reliable. On planted mistakes (a corner short of the
crease, one wing folded) Spark did not flag them on real stills; on hands-off photos it flagged one
once. No planted mistake produced a warning in any live run. Step tracking works; judging a
fold's precise geometry from a photo does not, yet. The next
step is measuring the work's outline from SAM's mask instead of asking a language model about it.

## Challenges
- The glasses' microphone ends the camera stream on current firmware, so speech goes through the
  phone.
- A head-worn camera never holds still, so "the work is finished" is detected from hands leaving
  the work rather than from the picture settling.
- Every still freezes the stream and waits for the voice to finish, so stills are taken only when
  the novice signals; timed checks use the 720p stream.

## What's next
Mistake detection from the work's silhouette; the glasses' touchpad as "next step" (the toolkit's
experimental Inputs); an expert review queue for disputed checks; the same pipeline on HVAC work
(thermostat wiring) with a working technician.

## Built during the hackathon
Everything in the repository was written from Oct 27, 2026, by one person with Claude Code, from a
plan drafted at kickoff. See BUILT_DURING_HACKATHON.md for provenance and commit gaps.

## Disclosures
- Voice: the demo guide speaks in a cloned voice of the expert (open-source Chatterbox, MIT), made
  locally from 30 s of the expert's own recording with their consent; Meta's MMS-TTS is the
  default. The clone runs on the laptop; the rendered lines are in the repository.
- The progress model uses DINOv2 (Meta, open weights); MMS-TTS is Meta, open weights.
