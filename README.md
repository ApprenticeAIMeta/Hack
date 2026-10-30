# ApprenticeAI

Capture how an expert does physical work through Meta AI glasses, and guide anyone wearing the
same glasses through it live: the phone shows each step and what it should look like next to the
wearer's own work, and the expert's recorded voice plays along.

Built for the Meta Global AI Developer Hackathon (Oct 27 to Nov 4, 2026). See [PLAN.md](PLAN.md).

## See it in 2 minutes, no glasses

```bash
cd backend && uv sync
PYTHONPATH=. .venv/bin/uvicorn apprentice.server:app --port 8000
```

Open <http://localhost:8000/?demo=1> for the full dashboard, and <http://localhost:8000/guide> at
phone size for the screen the wearer sees. Both replay a recorded run; no API key or glasses needed.
(The recording and its media get bundled in `demo/` once the final footage is shot; until then this
needs a local session in `out/sessions/`.)
Running live takes a `META_API_KEY` in `.env`, the glasses and the Android app in `android/`.
`SPEND_CAP_USD` (default 10) stops model calls at that total.

See [BUILT_DURING_HACKATHON.md](BUILT_DURING_HACKATHON.md) and [docs/rubric-map.md](docs/rubric-map.md).

## Built during the hackathon

Everything in this repository was written during the building period (from Oct 27, 2026).

## Pre-hackathon setup (not part of the submission)

Before the event: paired the Ray-Ban Meta glasses with a Pixel 10, ran Meta's open-source
Wearables Device Access Toolkit CameraAccess sample to confirm camera streaming, measured stream
quality settings, and recorded test footage. None of that code is in this repository.
