# Guide loop notes (Day 4, Oct 27)

Replay of the novice airplane run (7 fps glasses stream) through the full loop: 2 fps ticks,
progress model per tick, Spark checks with simulated latency. Results in `docs/eval.md`.

What changed while building it, and why
1. **Spark needs the workpiece, not the frame.** Sending the 2 fps proxy (a re-encode of the
   stream) got "heavily pixelated, cannot verify" on 49 of 52 checks. Sending the sharpest
   original stream frame of the last half second, cropped to the SAM workpiece box and enlarged,
   got a usable answer on 40 of 43.
2. **"Unclear" must be its own answer.** Folding it into "not done" reset the tracker on every
   blurry frame.
3. **Ask "how far along?", not "is step k done?".** Once the novice is past step k, the shape no
   longer matches k's reference picture and a yes/no check says "not done" forever (the tracker
   reached step 2 at 65 s). A locate check shows Spark every step's reference crop and asks how
   many are complete: steps passed on time, skipped steps are handled without being read out.
4. **A warning needs two positive mistake checks in a row.** The single-check version warned on
   compression artifacts ("ripped sheet"); with confirmation, 0 false warnings on the clean run.
5. **Novelty is relative.** Absolute similarity to the expert bank flagged 41% of a normal novice
   run as unseen (different room and light); a drop below the run's own recent median is used.

Known weak spots
- Step timing median error 11.8 s. Spark confuses adjacent states that differ by one fold
  (corners vs sharpened nose) and misses a center crease on the stream.
- Not yet measured: mistake catch rate and latency (needs recorded mistake runs).
