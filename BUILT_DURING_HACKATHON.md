# Built during the hackathon

First commit: `9e7648e`, 2026-10-27 15:00 ET. Check the history with:

```bash
git log --reverse --format='%h %ad %s' --date=iso
```

## How it was written

One person, with Claude Code (an AI coding assistant) writing most of the code from a plan
drafted at kickoff (PLAN.md). That is why the first afternoon carries several thousand lines
(compile, progress model, tracker, server, dashboard, Android client between 15:00 and 17:06 on
Oct 27): each piece was specified in the plan and written, run against recorded footage and
committed in turn. The repository is under an organization account (ApprenticeAIMeta) created for
the hackathon; its "Initial commit" (`32ac38b`) is GitHub's placeholder README, merged in
`012f343`. All commits are by the one builder.

Commit gaps: Oct 29 and Oct 31 to Nov 2 have no commits. The glasses were unavailable until the
night of Oct 31, then pairing, a factory reset and a firmware update took Nov 1 and 2. Work from
Nov 2 and 3 (the React pages, the first live runs and their fixes) was committed on Nov 3,
several of those commits within the same minutes.

## Written during the event

Everything in this repository: the compile pipeline, progress model, step tracker, live server,
dashboard, guide screen, Android app logic, localization, ask-the-expert, eval and docs.

## Existing before the event, and how it relates

- Pairing the Ray-Ban Meta glasses with a Pixel 10, running Meta's open-source Device Access
  Toolkit CameraAccess sample, and measuring stream settings. That code is not in this repository.
- The Android project's Gradle setup is adapted from that sample.
- A frame-selection script written on Oct 22 while measuring the stream (pick the sharpest frame
  per second, gated on head motion). `backend/apprentice/capture/frame_gate.py` is a rewrite of
  that idea during the event; the original is not in this repository.
- Test footage recorded on Oct 22, used only for development. It is not in this repository, and
  the submitted demo uses footage recorded during the event (Nov 3).

## Open source used

Meta Device Access Toolkit, meta-sam-parser, DINOv2 and MMS-TTS weights (Meta), Chatterbox (Resemble
AI, MIT; optional cloned voice), FastAPI, PyAV, OpenCV, NumPy, PyTorch, Transformers, OkHttp,
Jetpack Compose, React, Vite, Tailwind CSS, Framer Motion. Fonts: Geist and Geist Mono.
