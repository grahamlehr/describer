#!/usr/bin/env bash
# One-shot Pi setup: system packages, venv, Piper, cage, and the two services.
# Safe to re-run; every step is idempotent.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOICE="${VOICE:-en_GB-alan-medium}"
VOICES_DIR="$REPO_DIR/voices"
PIPER_DIR="$REPO_DIR/.piper"
PIPER_VERSION="${PIPER_VERSION:-2023.11.14-2}"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_aarch64.tar.gz"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

say "Installing system packages"
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  cage chromium-browser \
  alsa-utils curl ca-certificates \
  wlr-randr

say "Creating the Python environment"
cd "$REPO_DIR"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

say "Installing Piper"
if [ ! -x "$PIPER_DIR/piper/piper" ]; then
  mkdir -p "$PIPER_DIR"
  curl -fsSL "$PIPER_URL" | tar -xz -C "$PIPER_DIR"
fi
sudo ln -sf "$PIPER_DIR/piper/piper" /usr/local/bin/piper

say "Fetching the voice: $VOICE"
mkdir -p "$VOICES_DIR"
# Voice paths look like .../en_GB/alan/medium/en_GB-alan-medium.onnx
voice_rest="${VOICE#en_GB-}"
speaker="${voice_rest%%-*}"
quality="${voice_rest##*-}"
for suffix in onnx onnx.json; do
  target="$VOICES_DIR/$VOICE.$suffix"
  [ -f "$target" ] || curl -fsSL -o "$target" \
    "$VOICE_BASE/$speaker/$quality/$VOICE.$suffix"
done

say "Preparing configuration"
[ -f config.yaml ] || cp config.example.yaml config.yaml
if [ ! -f .env ]; then
  echo "RDM_API_KEY=" > .env
  chmod 600 .env
  echo "  Put your Rail Data Marketplace key in $REPO_DIR/.env before starting."
fi

say "Installing user services"
mkdir -p "$HOME/.config/systemd/user"
install -m 644 deploy/describer.service "$HOME/.config/systemd/user/describer.service"
install -m 644 deploy/kiosk.service "$HOME/.config/systemd/user/kiosk.service"
systemctl --user daemon-reload
systemctl --user enable describer.service kiosk.service
# Keep the services running when nobody is logged in.
sudo loginctl enable-linger "$USER"

say "Done"
cat <<MSG
Next steps:
  1. Put your API key in $REPO_DIR/.env  (RDM_API_KEY=...)
  2. systemctl --user restart describer kiosk
  3. Board:    http://$(hostname -I | awk '{print $1}'):8080/
     Settings: http://$(hostname -I | awk '{print $1}'):8080/admin
  Logs: journalctl --user -u describer -f
MSG
