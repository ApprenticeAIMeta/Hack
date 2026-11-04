# Planned features

Ideas beyond the working demo, kept here so the submission can say what comes next and so
nothing is built on a whim during the last day. Each entry says what it is, why a judge or a
user would care, what it costs, and what it depends on. Nothing listed here is built unless
the "status" line says so.

Judging weights, from the participant guide: impact 30%, Meta AI tooling 25%, demo 25%,
technical depth 20%. "Hours" are solo build-and-test estimates.

## Tier 1: build tonight if the live run is clean

### Ask the expert (built Nov 3)
The novice says "is this right?" or "which wire goes to Y?" into the phone. Muse Voice
Transcribe turns it into text, Muse Spark answers from the expert's transcript plus the current
still, and MMS-TTS speaks the answer. Turns a step checker into a tutor and gives the demo a
live question-and-answer beat. Mic is the phone's, not the glasses', because the glasses' mic
route ends the stream. About 1 hour. Depends on nothing new.

### Skill report card
When the run ends, the phone and dashboard show: time per step against the expert, mistakes
caught and fixed, photos used, cost, and each step's "done" still next to the expert's. Every
number is already logged, so this is mostly layout. Gives the video an ending and gives judges
proof the system saw the whole job. About 30 minutes.

### Cost and privacy line
"This run: $0.46, 24 Spark checks, 13 stills, nothing leaves the laptop except model calls."
Shows the project is priced and shippable. 15 minutes.

## Tier 2: after the video is safe

### Any language (built Nov 3)
compile/localize.py: Muse Spark translates the spoken and shown lines, MMS-TTS speaks them in
that language; Hindi rendered for the airplane. The checks stay in English.

### Expert's own voice (built, generating)
Chatterbox (Resemble AI, MIT) clones the expert from 30 s of the narration, on the laptop's GPU.
Too slow for live lines (1 to 3 minutes each on an M2), so every instruction and a warning for
every known mistake are rendered after the compile; a warning Spark describes in other words
falls back to a generic cloned line, then to MMS-TTS. Voice audio never leaves the machine.

### Mistakes that teach the next novice
Every warning the novice confirms with "Got it" is appended to that step's known mistakes with
the still that triggered it. The procedure gets better with each apprentice, which is the
product's long-term moat. Small write to procedure.json plus a confirm message. 30 minutes.

### Second procedure in the video
Desk-fan cleaning or a climbing knot, captured in 2 minutes. One task looks like a trick; two
looks like a product. Only after the airplane is bulletproof. 1.5 hours including filming.

### Open-weights badge
One compile run on Muse Spark Open (Baseten) and all speech on MMS-TTS, with a "runs on open
weights" badge on the dashboard. Answers "what if the API goes away". Compile already works on
Spark Open (docs/open-weights.md); live checks are too slow there. 30 minutes.

## Tier 3: the bigger ideas, for the write-up and for after the hackathon

### Expert review queue
Novice runs that ended with an unresolved warning, or that the novice flagged, go to the expert
as a 30-second clip with the system's reasoning. The expert taps "right" or "wrong", and the
tap becomes a label that retunes the warning threshold for that step. Closes the loop between
the two people the product exists for. Needs a tiny review page and a labels file. 2 hours.

### Procedure diff between two experts
Record the same job from two experts; Spark aligns the two step lists and shows where they
differ (order, a step one of them skips, a different "why"). Surfaces tacit knowledge that no
manual records, which is the impact story made visible. 2 hours, needs a second expert.

### Pace coaching
The progress model already knows where the expert was at every second. Speak "you're about a
minute behind the expert on this step, that's normal" or "you're moving faster than the expert
did here, check the crease" at the right moments. Turns a number on the dashboard into
guidance. 1 hour; the risk is nagging, so keep it to one line per step.

### Hands-free confirmation
Replace the "Check my step" tap with a gesture the glasses can see: both palms flat on the
table for 2 seconds. SAM already tracks hands, so this is a rule on the existing boxes. Keeps
both hands in the work and looks great on video. 1 hour; needs testing against false triggers.

### Audio-only fallback with a cheap earpiece
For jobs where the phone can't be propped (overhead work, crawlspaces), guidance runs voice
only with the spoken "should look like" description and a longer spoken check answer. The
compile already produces the text; this is a phone setting. 45 minutes.

### Certification record
Each completed run signs a small JSON record: procedure hash, steps, stills, warnings, timings.
A supervisor or a trade school can verify that this person did this job under guidance on this
date. Pairs with the report card. 1 hour.

### Multi-step lookahead on the phone
Show the next step's target photo dimmed under the current one, so the novice knows what the
work is heading toward. Cheap, reduces "which fold is this" confusion. 20 minutes.

### Procedure marketplace
Experts publish compiled procedures (the compiled JSON, stills and spoken lines are small). A
trade school or a company keeps its own library. Business model more than a feature; one slide
in the write-up, no code.

## Rejected
- Hosted voice cloning (ElevenLabs and similar): allowed by the rules, which require Meta models but do not bar others, but the expert's voice should not leave the laptop. Open-source cloning (Chatterbox, MIT) runs locally instead; see "Expert's own voice" below.
- AR overlays on the glasses: there is no display.
- Accounts, cloud sync, multi-user: no rubric points, lots of surface area.
- Shortening the catch delay in the edit: any sped-up section is labeled on screen instead.
