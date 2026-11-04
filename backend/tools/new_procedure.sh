#!/usr/bin/env bash
# From an expert recording on the phone to a live guide, in one command.
#
#   tools/new_procedure.sh <name> <phone video file name> [second recording] [voice sample on the phone]
#
# e.g. tools/new_procedure.sh thermostat 20261104_093012_ab12cd34.mp4 20261104_094501_ef56ab78.mp4
#
# Pulls the recordings from the phone's Download/Meta AI folder (share them there from Meta AI:
# Share > Share to > Files: Download), compiles the first one (Voice Transcribe + Muse Spark),
# trains the progress model on all of them, re-picks the reference photos with hands off the work,
# and, if a voice sample is given, renders every line in that voice with Chatterbox Turbo. Each
# stage prints its timing; the guide can start as soon as training finishes (MMS speaks until the
# cloned lines land). Run from backend/.
set -euo pipefail
name=${1:?procedure name}; first=${2:?first recording}; second=${3:-}; sample=${4:-}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PHONE="/sdcard/Download/Meta AI"
t0=$(date +%s); stamp() { echo "[$(( $(date +%s) - t0 ))s] $*"; }

# A path that exists on the laptop is used as is; anything else is pulled from the phone
pull() {
  if [ -f "$1" ]; then cp "$1" "$ROOT/footage/${name}_$2.mp4" && stamp "copied $1 -> footage/${name}_$2.mp4"
  else adb pull "$PHONE/$1" "$ROOT/footage/${name}_$2.mp4" >/dev/null && stamp "pulled $1 -> footage/${name}_$2.mp4"; fi
}
pull "$first" expert1
[ -n "$second" ] && pull "$second" expert2

stamp "compiling (Voice Transcribe, Muse Spark)"
PYTHONPATH=. .venv/bin/python -m apprentice.compile.build "$ROOT/footage/${name}_expert1.mp4" --name "$name" 2>&1 \
  | grep -vE "Warning|warn|Loading weights|Redirects"

stamp "training the progress model and picking reference photos"
runs="expert1=$ROOT/footage/${name}_expert1.mp4"
[ -n "$second" ] && runs="$runs expert2=$ROOT/footage/${name}_expert2.mp4"
PYTHONPATH=. .venv/bin/python -m apprentice.progress.train "$name" $runs 2>&1 | grep -vE "Warning|warn|Loading weights|Redirects"

stamp "ready: start the guide with procedure \"$name\""

if [ -n "$sample" ]; then
  mkdir -p "$ROOT/out/voice/$name"
  adb pull "$PHONE/$sample" "$ROOT/out/voice/$name/sample_raw" >/dev/null
  # 16-24 kHz mono, denoised and level-matched: the cleaner the reference, the better the clone
  ffmpeg -v error -y -i "$ROOT/out/voice/$name/sample_raw" -vn -af "afftdn=nf=-28,loudnorm=I=-18:TP=-2" \
    -ac 1 -ar 24000 -t 30 "$ROOT/out/voice/$name/reference.wav"
  stamp "rendering every line in the sampled voice (Chatterbox Turbo, about 20 s a line)"
  NAME="$name" ROOT="$ROOT" ~/.venvs/clone/bin/python - <<'PY'
import json, os, time, torch, torchaudio as ta
from pathlib import Path
from chatterbox.tts_turbo import ChatterboxTurboTTS
root, name = Path(os.environ["ROOT"]), os.environ["NAME"]
proc = json.load(open(root / "procedures" / name / "procedure.json"))
out = root / "procedures" / name / "voice"; out.mkdir(exist_ok=True)
lines = {"generic_warn": "Stop. Check this against the expert's work before you go on."}
for s in proc["steps"]:
    lines[f"{s['id']}_say"] = s["instruction"]
    for j, m in enumerate(s["known_mistakes"]):
        lines[f"{s['id']}_warn{j + 1}"] = f"Stop. It looks like you're {m}. " + (
            s["why"].capitalize() + "." if s["why"] else "Compare it with the expert's.")
m = ChatterboxTurboTTS.from_pretrained(device="mps" if torch.backends.mps.is_available() else "cpu")
ref = str(root / "out" / "voice" / name / "reference.wav")
for k, text in lines.items():
    t = time.time()
    ta.save(str(out / f"{k}.wav"), m.generate(text, audio_prompt_path=ref), m.sr)
    print(f"  {k} {time.time() - t:.0f}s", flush=True)
json.dump(lines, open(out / "lines.json", "w"), indent=1)
PY
  stamp "cloned lines done; new sessions speak in that voice"
fi
