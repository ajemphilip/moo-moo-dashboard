#!/bin/bash
# Sets up Moo Local on this computer, so a Moo on the same home network can
# use it for listening, stories and voice. Offered by the Moo itself:
#
#     curl -fsSL http://moo.local:8000/local-ai/install.sh | bash
#
# It needs a Mac with Apple silicon and Homebrew. It installs Python 3.12 and
# Ollama with Homebrew if they are missing, puts Moo Local into ~/.moo-local,
# downloads one story model and the voices (about 5 GB), and starts Moo Local.
# Run it again at any time to update. Nothing is set to start at login.
set -euo pipefail

MOO="${MOO_URL:-__MOO_URL__}"
APP="$HOME/.moo-local/app"
VENV="$HOME/.moo-local-venv"
STORY_MODEL="${MOO_LOCAL_CHAT_MODEL:-gemma3:4b}"

say() { printf '\n==> %s\n' "$1"; }

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "Moo Local needs a Mac with Apple silicon (M1 or newer)."
  echo "On another computer, open the Moo website in its browser and choose This device."
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  echo "Moo Local needs Homebrew. Install it from https://brew.sh and run this again."
  exit 1
fi

say "Python 3.12 and Ollama"
brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12
command -v ollama >/dev/null 2>&1 || brew install ollama
if ! curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  (nohup ollama serve >"$HOME/.moo-local-ollama.log" 2>&1 &)
  for _ in $(seq 1 20); do curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
fi

say "Moo Local from $MOO"
mkdir -p "$APP" "$HOME/.moo-local/kokoro" "$HOME/.moo-local/voices"
for f in moo_local.py mlx_voice_worker.py kitten2_worker.py requirements-base.txt requirements.txt requirements-kitten2.txt; do
  curl -fsSL "$MOO/local-ai/files/$f" -o "$APP/$f"
done

say "Python libraries (a few minutes the first time)"
PY="$(brew --prefix python@3.12)/bin/python3.12"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP/requirements-base.txt"

say "Voices"
K="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
[ -s "$HOME/.moo-local/kokoro/kokoro-v1.0.onnx" ] || curl -fL --progress-bar "$K/kokoro-v1.0.onnx" -o "$HOME/.moo-local/kokoro/kokoro-v1.0.onnx"
[ -s "$HOME/.moo-local/kokoro/voices-v1.0.bin" ] || curl -fL --progress-bar "$K/voices-v1.0.bin" -o "$HOME/.moo-local/kokoro/voices-v1.0.bin"
P="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US"
for v in amy/medium/en_US-amy-medium kristin/medium/en_US-kristin-medium ryan/high/en_US-ryan-high; do
  n="$(basename "$v")"
  [ -s "$HOME/.moo-local/voices/$n.onnx" ] || curl -fsSL "$P/$v.onnx" -o "$HOME/.moo-local/voices/$n.onnx"
  [ -s "$HOME/.moo-local/voices/$n.onnx.json" ] || curl -fsSL "$P/$v.onnx.json" -o "$HOME/.moo-local/voices/$n.onnx.json"
done

say "Story model $STORY_MODEL (about 3 GB the first time)"
ollama pull "$STORY_MODEL"

say "Starting Moo Local"
pkill -f "$APP/moo_local.py" 2>/dev/null || true
sleep 1
(cd "$APP" && MOO_LOCAL_CHAT_MODEL="$STORY_MODEL" nohup "$VENV/bin/python" "$APP/moo_local.py" >>"$HOME/.moo-local/server.log" 2>&1 &)
for _ in $(seq 1 40); do curl -fs http://127.0.0.1:8765/health >/dev/null 2>&1 && break; sleep 1; done
if curl -fs http://127.0.0.1:8765/health >/dev/null 2>&1; then
  echo
  echo "Moo Local is running on this computer. On the Moo website, open AI engine:"
  echo "this computer now appears under A computer. Choose it there."
  echo
  echo "After a restart of this computer, start Moo Local again with:"
  echo "  cd $APP && nohup $VENV/bin/python moo_local.py >> ~/.moo-local/server.log 2>&1 &"
else
  echo "Moo Local did not start. See ~/.moo-local/server.log"
  exit 1
fi
