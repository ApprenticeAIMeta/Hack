# ApprenticeAI

An expert does a job once wearing Ray-Ban Meta glasses and talks through it. Anyone who puts the
glasses on after them is guided through the same job step by step: the phone shows each step and
what the expert's work looked like when it was done, a voice says what to do, the guide moves on
when the novice takes their hands off the work and Muse Spark agrees the step is done, and the
novice can ask the expert a question out loud at any point. It works in the novice's language.

Built for the Meta Global AI Developer Hackathon (Oct 27 to Nov 4, 2026).

**Status, honestly:** live step-by-step guidance, hands-off advancing, spoken questions and the
Hindi guide work on a real run (`demo/catch5`: all seven steps of a paper airplane, including
throwing it). Catching a mistake automatically does not work reliably yet: planted mistakes were
missed in every run so far ([docs/eval.md](docs/eval.md)).

## See it in 2 minutes, no glasses, no API key

```bash
cd backend && uv sync
PYTHONPATH=. .venv/bin/uvicorn apprentice.server:app --port 8000
```

Open <http://localhost:8000/?demo=1> for the dashboard and <http://localhost:8000/guide> at phone
size for the screen the wearer sees. Both replay `demo/catch5`, a recorded live run, with every
model call and its reason; "Replay a run" on the dashboard picks another saved run and a speed.

Running live takes a `META_API_KEY` in `.env`, the glasses (Developer Mode on for them in Meta
AI) and the Android app in `android/`. `SPEND_CAP_USD` (default 10) stops model calls at that
total. A new procedure from an expert recording is one command: `backend/tools/new_procedure.sh`.

## What each model does

| Model | Job |
|---|---|
| Muse Voice Transcribe | Times the expert's narration, so steps, captions and known mistakes come from their words; transcribes the novice's spoken questions (about 1.3 s) |
| Muse Spark 1.3 | Compiles the recording and transcript into steps; live, judges which step the novice has reached from the stream or a photo; answers the novice's questions as the expert, from the recording and the current photo; translates the guide |
| SAM 3.1 | Tracks the workpiece in every stream frame, finds it in each photo, detects whether a hand is on it (the hands-off signal), and picks the expert's reference photos with the hands off |
| MMS-TTS (Meta, open weights) | Speaks instructions, warnings and answers, in any of its 1,100+ languages |
| DINOv2 (Meta, open weights) | Embeds workpiece crops for the progress model and the dashboard's "expert at the same point" view |

Voice: the bundled demo speaks in an open-source voice clone of the expert (Chatterbox, MIT),
rendered on the laptop from 30 s of the expert's own narration, with their consent; MMS-TTS is the
default and the fallback. Live answers to questions are always MMS-TTS.

The web pages are a Vite + React + Tailwind + Framer Motion build in `web/app`, committed as
`web/build` so the server runs without Node.

More: [docs/rubric-map.md](docs/rubric-map.md) (where each judging criterion is evidenced),
[docs/eval.md](docs/eval.md) (every live run, misses included),
[BUILT_DURING_HACKATHON.md](BUILT_DURING_HACKATHON.md) (provenance).
