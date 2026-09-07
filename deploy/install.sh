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
ENV_FILE="${ENV_FILE:-/etc/describer/describer.env}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

say "Installing system packages"
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  cage chromium \
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

# Credentials for both sources live in one root-owned-directory file, readable
# only by the user the services run as. They never go near config.yaml.
sudo mkdir -p "$(dirname "$ENV_FILE")"
[ -f "$ENV_FILE" ] || sudo install -m 600 -o "$USER" /dev/null "$ENV_FILE"

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

ask() {  # ask <VAR> <prompt> [silent]
  local current="${!1:-}" answer=""
  if [ ! -t 0 ]; then
    echo "  Not a terminal; leaving $1 as it is."
    return
  fi
  if [ -n "$current" ]; then
    read -r -p "  $2 [keep existing]: " answer
  elif [ "${3:-}" = "silent" ]; then
    read -r -s -p "  $2: " answer; echo
  else
    read -r -p "  $2: " answer
  fi
  [ -n "$answer" ] && printf -v "$1" '%s' "$answer"
}

echo "Credentials (leave blank to keep what is already there):"
ask RDM_API_KEY "Rail Data Marketplace API key" silent
ask RTT_TOKEN "Realtime Trains token (api-portal.rtt.io)" silent

quote() {  # single-quote so a password with spaces survives systemd and dotenv
  case "$1" in
    *\'*)
      echo "  A single quote in a credential cannot be written safely." >&2
      echo "  Put that value in $ENV_FILE by hand." >&2
      printf "''"
      return
      ;;
  esac
  printf "'%s'" "$1"
}

umask 077
cat > "$ENV_FILE" <<ENV
# Written by deploy/install.sh. Credentials only — never commit this file.
RDM_API_KEY=$(quote "${RDM_API_KEY:-}")
RTT_TOKEN=$(quote "${RTT_TOKEN:-}")
ENV
chmod 600 "$ENV_FILE"

say "Installing services"
# The backend is a user service so it can find the repo and venv under $HOME.
mkdir -p "$HOME/.config/systemd/user"
install -m 644 deploy/describer.service "$HOME/.config/systemd/user/describer.service"
systemctl --user daemon-reload
systemctl --user enable describer.service
# Keep the backend running when nobody is logged in.
sudo loginctl enable-linger "$USER"

# A blank cursor theme. cage parks a pointer in the middle of the screen even
# with no mouse attached, and has no flag to hide it. It builds its cursor
# manager with a null theme name and a hardcoded size, so XCURSOR_THEME and
# XCURSOR_SIZE are both ignored: it loads whatever theme is named "default".
# So we ship a theme *called* default, of transparent pixels, and put its
# directory first on XCURSOR_PATH. (The board's own `cursor: none` covers a
# real pointer over the window, which with no mouse never happens.)
say "Installing the blank cursor theme"
CURSOR_DIR=/usr/local/share/describer-cursors
# install -d, not mkdir -p: mkdir takes the caller's umask, and a umask of 077
# leaves a theme cage itself cannot read, which it ignores in silence.
sudo install -d -m 755 "$CURSOR_DIR" "$CURSOR_DIR/default" "$CURSOR_DIR/default/cursors"
python3 -c '
import struct, sys
# Xcursor: header, one TOC entry, one 24x24 fully transparent ARGB image.
# 24 is the size cage asks for, and a whole image of zeroes is safer than a
# single pixel a loader might reject.
size = 24
out = struct.pack("<4sIII", b"Xcur", 16, 0x00010000, 1)
out += struct.pack("<III", 0xfffd0002, size, 28)
out += struct.pack("<IIIIIIIII", 36, 0xfffd0002, size, 1, size, size, 0, 0, 0)
out += b"\x00" * (size * size * 4)
sys.stdout.buffer.write(out)
' | sudo install -m 644 /dev/stdin "$CURSOR_DIR/default/cursors/default"
for name in left_ptr arrow top_left_arrow pointer hand1 hand2 xterm text watch; do
  sudo ln -sf default "$CURSOR_DIR/default/cursors/$name"
done
printf '[Icon Theme]\nName=default\n' \
  | sudo install -m 644 /dev/stdin "$CURSOR_DIR/default/index.theme"
# Answer to the name as well, in case a later cage does read XCURSOR_THEME.
sudo ln -sfn default "$CURSOR_DIR/describer-blank"
# The first attempt, which asked for a theme by name and was never read.
sudo rm -rf /usr/share/icons/describer-blank

# The kiosk is a system service on tty1: cage needs a real seat, which a
# lingering user session never gets. It runs as this user so it can reach the
# audio and video devices the same way an interactive login would.
sudo usermod -aG video,render,input,audio "$USER"
sed -e "s|@USER@|$USER|g" -e "s|@UID@|$(id -u)|g" deploy/kiosk.service \
  | sudo install -m 644 /dev/stdin /etc/systemd/system/kiosk.service
# The old user-level kiosk unit, if one is left from an earlier install.
if [ -f "$HOME/.config/systemd/user/kiosk.service" ]; then
  systemctl --user disable --now kiosk.service || true
  rm -f "$HOME/.config/systemd/user/kiosk.service"
fi
sudo systemctl daemon-reload
sudo systemctl set-default graphical.target
sudo systemctl enable kiosk.service

say "Done"
cat <<MSG
Next steps:
  1. Check your credentials in $ENV_FILE
     (RDM_API_KEY for the Rail Data Marketplace, RTT_TOKEN for Realtime
      Trains — the fallback source)
  2. systemctl --user restart describer && sudo systemctl restart kiosk
  3. Board:    http://$(hostname -I | awk '{print $1}'):8080/
     Settings: http://$(hostname -I | awk '{print $1}'):8080/admin
  Logs: journalctl --user -u describer -f   and   sudo journalctl -u kiosk -f
MSG
