#!/bin/bash
# Sets up Moo Local on this computer (macOS or Linux), so a Moo on the same
# home network can use it for listening, stories and voice. The Moo hands it
# out as a file to double-click ("Install Moo Local.command" on a Mac,
# "Install Moo Local.sh" on Linux) and as a Terminal line:
#
#     curl -fsSL http://moo.local:8000/local-ai/install.sh | bash
#
# Everything goes into the home folder; nothing needs an administrator,
# except Ollama's own installer on Linux, which asks for the password once.
#   ~/.moo-local/app          Moo Local itself, fetched from the Moo
#   ~/.moo-local/bin          uv, which brings its own Python 3.12
#   ~/.moo-local-venv         Moo Local's libraries
#   ~/.moo-kitten2-venv       Kitten TTS 2's libraries
#   ~/.moo-local/ollama       Ollama, on a Mac that does not have it yet
# It downloads no AI models: the parent chooses them afterwards on the Moo's
# AI engine screen, and Moo Local refuses one too big for this computer. It
# starts Moo Local and sets it to start again at every login. Run it again
# at any time to update.
set -uo pipefail

MOO="${MOO_URL:-__MOO_URL__}"
ROOT="$HOME/.moo-local"
APP="$ROOT/app"
VENV="$HOME/.moo-local-venv"
KVENV="$HOME/.moo-kitten2-venv"
UV_VERSION="0.13.9"
OS="$(uname -s)"
ARCH="$(uname -m)"

# Opened from a Linux file manager there may be no window; open one, so the
# progress shows and Ollama's installer can ask for the password.
if [ "$OS" = "Linux" ] && [ ! -t 1 ] && [ -z "${MOO_IN_TERMINAL:-}" ] && [ -f "${BASH_SOURCE[0]:-}" ]; then
  export MOO_IN_TERMINAL=1
  for t in x-terminal-emulator gnome-terminal konsole xfce4-terminal mate-terminal kitty alacritty xterm; do
    if command -v "$t" >/dev/null 2>&1; then
      case "$t" in
        gnome-terminal) exec "$t" -- bash "${BASH_SOURCE[0]}" ;;
        *) exec "$t" -e bash "${BASH_SOURCE[0]}" ;;
      esac
    fi
  done
fi

say() { printf '\n==> %s\n' "$1"; }
fail() {
  printf '\nMoo Local could not be set up: %s\n' "$1"
  printf 'The details are in %s. Running this again continues where it stopped.\n' "$ROOT/install.log"
  if [ -t 0 ]; then printf 'Press Return to close this window. '; read -r _; fi
  exit 1
}
done_ok() {
  if [ -t 0 ] && [ -z "${MOO_NO_PAUSE:-}" ]; then printf '\nPress Return to close this window. '; read -r _; fi
  exit 0
}

mkdir -p "$APP" "$ROOT/bin" "$ROOT/kokoro" "$ROOT/voices"
exec > >(tee -a "$ROOT/install.log") 2>&1
echo "--- $(date) $OS $ARCH, from $MOO"

if [ "$OS" != "Darwin" ] && [ "$OS" != "Linux" ]; then
  fail "this installer is for macOS and Linux. On Windows, use Install Moo Local.cmd from the Moo website."
fi
command -v curl >/dev/null 2>&1 || fail "curl is missing. Install it (for example: sudo apt install curl) and run this again."
if [ "$OS" = "Darwin" ] && [ "$ARCH" != "arm64" ]; then
  echo "This is a Mac with an Intel processor. Moo Local works, with fewer voices:"
  echo "Pocket and Kitten 2 need a newer PyTorch than Intel Macs can have."
fi

# --- Moo Local's files, from the Moo ---------------------------------------
say "Moo Local from the Moo ($MOO)"
for f in moo_local.py mlx_voice_worker.py kitten2_worker.py requirements-base.txt requirements.txt requirements-kitten2.txt; do
  curl -fsSL "$MOO/local-ai/files/$f" -o "$APP/$f.new" || fail "the Moo at $MOO did not answer. Is this computer on the same wifi as the Moo?"
  mv "$APP/$f.new" "$APP/$f"
done

# --- uv and Python ------------------------------------------------------------
say "Python 3.12 (through uv)"
UV="$ROOT/bin/uv"
if [ ! -x "$UV" ] || ! "$UV" --version 2>/dev/null | grep -q "$UV_VERSION"; then
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_INSTALL_DIR="$ROOT/bin" INSTALLER_NO_MODIFY_PATH=1 sh \
    || fail "uv could not be downloaded. Check the internet connection."
fi
export UV_PYTHON_PREFERENCE=only-managed  # uv's own Python, the same on every computer
TORCH=()
[ "$OS" = "Linux" ] && TORCH=(--torch-backend cpu)  # PyTorch's NVIDIA build is 3 GB more and not needed here

# --- Ollama ---------------------------------------------------------------------
say "Ollama, for the stories"
OLLAMA="$(command -v ollama || true)"
for c in /opt/homebrew/bin/ollama /usr/local/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama "$ROOT/ollama/ollama"; do
  [ -z "$OLLAMA" ] && [ -x "$c" ] && OLLAMA="$c"
done
if [ -z "$OLLAMA" ]; then
  if [ "$OS" = "Darwin" ]; then
    mkdir -p "$ROOT/ollama"
    curl -fL --progress-bar https://ollama.com/download/ollama-darwin.tgz -o "$ROOT/ollama/ollama.tgz" \
      && tar xzf "$ROOT/ollama/ollama.tgz" -C "$ROOT/ollama" && rm -f "$ROOT/ollama/ollama.tgz" \
      || fail "Ollama could not be downloaded."
    OLLAMA="$ROOT/ollama/ollama"
  else
    echo "Ollama's own installer asks for this computer's password."
    curl -fsSL https://ollama.com/install.sh | sh || fail "Ollama's installer did not finish."
    OLLAMA="$(command -v ollama || echo /usr/local/bin/ollama)"
  fi
fi
if ! curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  (nohup "$OLLAMA" serve >"$ROOT/ollama.log" 2>&1 &)
  for _ in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
fi
curl -fs http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || fail "Ollama did not start. See $ROOT/ollama.log"

# --- Python libraries -------------------------------------------------------------
say "Python libraries (several minutes the first time)"
[ -x "$VENV/bin/python" ] || "$UV" venv --python 3.12 "$VENV" || fail "Python 3.12 could not be set up."
"$UV" pip install --python "$VENV/bin/python" ${TORCH[@]+"${TORCH[@]}"} -r "$APP/requirements-base.txt" \
  || fail "the Python libraries could not be installed."
if [ "$OS" = "Linux" ] || [ "$ARCH" = "arm64" ]; then
  say "Kitten TTS 2 (its own libraries)"
  [ -x "$KVENV/bin/python" ] || "$UV" venv --python 3.12 --seed "$KVENV" || fail "Python 3.12 could not be set up for Kitten 2."
  "$UV" pip install --python "$KVENV/bin/python" ${TORCH[@]+"${TORCH[@]}"} -r "$APP/requirements-kitten2.txt" \
    || echo "Kitten TTS 2 could not be installed; Moo Local works without it."
fi

# --- Start now and at every login ------------------------------------------------
say "Starting Moo Local"
if [ "$OS" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/local.moo.moo-local.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  pkill -f "$APP/moo_local.py" 2>/dev/null || true
  cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.moo.moo-local</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$APP/moo_local.py</string>
    <string>--log</string>
    <string>$ROOT/server.log</string>
  </array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>EnvironmentVariables</key>
  <dict><key>MOO_OLLAMA_BIN</key><string>$OLLAMA</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
</dict>
</plist>
EOF
  if [ -n "${MOO_NO_LOGIN_ITEM:-}" ]; then
    rm -f "$PLIST"
    (cd "$APP" && MOO_OLLAMA_BIN="$OLLAMA" nohup "$VENV/bin/python" "$APP/moo_local.py" --log "$ROOT/server.log" >/dev/null 2>&1 &)
  else
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
  fi
else
  pkill -f "$APP/moo_local.py" 2>/dev/null || true
  UNIT="$HOME/.config/systemd/user/moo-local.service"
  if [ -z "${MOO_NO_LOGIN_ITEM:-}" ] && command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$(dirname "$UNIT")"
    cat >"$UNIT" <<EOF
[Unit]
Description=Moo Local, the Moo's AI on this computer
After=network-online.target

[Service]
WorkingDirectory=$APP
Environment=MOO_OLLAMA_BIN=$OLLAMA
ExecStart=$VENV/bin/python $APP/moo_local.py --log $ROOT/server.log
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable moo-local.service >/dev/null 2>&1
    systemctl --user restart moo-local.service
  else
    if [ -z "${MOO_NO_LOGIN_ITEM:-}" ]; then
      mkdir -p "$HOME/.config/autostart"
      cat >"$HOME/.config/autostart/moo-local.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Moo Local
Exec=$VENV/bin/python $APP/moo_local.py --log $ROOT/server.log
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
    fi
    (cd "$APP" && MOO_OLLAMA_BIN="$OLLAMA" nohup "$VENV/bin/python" "$APP/moo_local.py" --log "$ROOT/server.log" >/dev/null 2>&1 &)
  fi
fi

for _ in $(seq 1 60); do curl -fs http://127.0.0.1:${MOO_LOCAL_PORT:-8765}/health >/dev/null 2>&1 && break; sleep 1; done
curl -fs http://127.0.0.1:${MOO_LOCAL_PORT:-8765}/health >/dev/null 2>&1 || fail "Moo Local did not start. See $ROOT/server.log"
cat <<EOF

Moo Local is running on this computer, and starts by itself at every login.
On the Moo website, open AI engine: this computer appears under Local
machines. Choose it there, then pick the story, listening and voice models
to download under Models. This window can be closed.
EOF
done_ok
