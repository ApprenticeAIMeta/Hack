# Meta Model API notes (Day 1 spike, Oct 27)

Measured with `backend/spike/api_spike.py` on the paper-airplane footage. Base URL
`https://api.meta.ai/v1`, bearer key, standard mode (not Contributor: technician footage must not
become training data). Whole spike cost about $0.31.

## Results

| Call | Input | Latency | Cost/call | Verdict |
|---|---|---|---|---|
| Voice Transcribe, ENDPOINTING | 2:17 expert2 wav, 16 kHz | 21.5 s (RTF 0.16) | $0.007 | Good text, 12 turns, usable turn times |
| Voice Transcribe, any mode | 2:02 expert1 wav (noisy room) | 15-17 s | $0.006 | Fails: 1-3 fragments. Spark audio also says "crosstalk, no clear narrator" |
| Voice Transcribe, PUSH_TO_TALK | either clip | 14-15 s | $0.006 | Empty transcript on long clips; do not use for files |
| Spark 1.2 audio | 2:17 expert2 wav | 11 s | $0.007 | Cleanest text, but timestamps compressed (ends at 1:26 on a 2:17 clip). Text only, never timing |
| Spark 1.3 video compile | expert2, 540p 10 fps, 6.4 MB, uploaded | 24 s (first token 18.7 s) | $0.059 (37k in, 2.8k out) | 8 steps, sensible why/done_check/mistakes, step times line up with ASR turns. Quotes are paraphrased, not verbatim |
| Spark 1.3 image, label OCR | 1080x1440 SDK photo | 9.5 s (low effort) | $0.006 | Large text exact, small print correctly flagged illegible instead of invented |
| Spark 1.3 done check, minimal effort | 504x896 stream frame, JSON schema | 0.7-2.0 s | $0.0014 | 3/3 consistent on the true positive; 1 false positive (flat sheet at an angle read as a point, conf 0.65); true negative at conf 0.95 |
| Spark 1.3 done check, low effort | same | 1.8-3.0 s | $0.002 | No better than minimal on this case |
| SAM 3.1 image | 768x1024 keyframe, "paper" / "hand" | 0.6-0.9 s | $0.0025 | Correct boxes: sheet 0.93, both hands 0.98 / 0.98, one distractor (receipt strip) at 0.62 |
| SAM 3.1 video | novice 7 fps stream, 800 frames, uploaded | 32.8 s (first frame 1.8 s) | $0.16 | Workpiece tracked as one stable id on 735/800 frames, mean conf 0.96; small paper scraps get their own ids (filter by area) |

## Decisions this forces

1. **Narration audio is the weakest link.** The glasses mic in a noisy room produced an
   untranscribable expert run. Expert sessions need a quiet room or the expert speaking up, and the
   capture app should check audio level live (warn below about -30 dB mean). Run Transcribe on every
   expert clip right after recording, before the expert leaves.
2. **Voice clips are cut from Transcribe turn times**, never from Spark timestamps. Turns are
   coarse (2-20 s), so clips are cut on turn boundaries and assigned to steps by overlap with the
   step's time range. Strip leaked `<|speaker_X|>` tokens from ENDPOINTING text.
3. **Step `quote` comes from the transcript, not from Spark.** Spark paraphrases ("Take one sheet
   out of the notebook" was never said). Compile passes the turn list into the prompt and asks Spark
   to pick turn ids per step; the clip is the real audio.
4. **Compile = one video call.** 540p at 10 fps with embedded audio is enough for structure; about
   $0.06 per expert run, so compiling repeatedly while iterating is fine.
5. **Single-frame done checks are not trustworthy alone.** A sub-2 s check is fast enough for live
   ticks, but viewpoint fools it. Use it as a confirmation gated by the progress model, send the
   expert's reference keyframe alongside the live frame, and require two consecutive agreeing checks
   above 0.8 before advancing.
6. **SAM 3.1 is fast enough to run live on images** (under 1 s), and video tracking runs at about
   24 frames/s server-side, faster than the 7 fps stream. Workpiece crops for the progress model come
   from the largest high-confidence "paper"/workpiece id.

## Still to measure

- Spark Open / Glimmer latency on Baseten (needs the Baseten credit grant)
- Done check with a reference keyframe as a second image (expect fewer viewpoint false positives)
- Manual diff: Spark with a PDF plus a video in one request
- SAM on the native 1536x2048 expert clip (3,600 frames, about $0.73), only if crops from 540p are too coarse
