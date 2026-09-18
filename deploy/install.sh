#!/usr/bin/env bash
# Pi setup, root-run and in two parts:
#
#   sudo deploy/install.sh provision   # packages, the describer user, the
#                                       # checkout, units. Chroot-safe: used
#                                       # both on a real Pi and inside the
#                                       # pi-gen image build. No `systemctl
#                                       # start`/`restart`/`is-active`/
#                                       # `daemon-reload`, no `hostname -I`.
#   sudo deploy/install.sh configure   # asks for credentials, starts services.
#                                       # Needs a real system with a terminal.
#   sudo deploy/install.sh all         # provision, then configure. The default.
#
# Safe to re-run; every step is idempotent.
set -euo pipefail

#: Where the code ends up. Fixed: this is the one layout, not configurable.
TARGET_DIR=/opt/describer
#: Where *this* script lives, which may be a different checkout (a migration
#: from an old install, or the pi-gen image build's throwaway clone).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/etc/describer/describer.env}"
CONFIG_FILE=/etc/describer/config.yaml
VOICE="${VOICE:-en_GB-alan-medium}"
PIPER_VERSION="${PIPER_VERSION:-2023.11.14-2}"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_aarch64.tar.gz"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0 ${1:-}" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# provision: chroot-safe. No running systemd assumed or required.
# ---------------------------------------------------------------------------

install_packages() {
  say "Installing system packages"
  apt-get update
  apt-get install -y \
    python3 python3-venv python3-pip \
    cage chromium \
    alsa-utils curl ca-certificates \
    wlr-randr git \
    python3-gpiozero python3-lgpio
}

create_user() {
  say "Creating the describer user"
  if ! id -u describer >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/describer --create-home \
      --shell /usr/sbin/nologin describer
  fi
  usermod -aG video,render,input,audio,gpio describer
  install -d -m 755 -o describer -g describer /var/lib/describer
}

get_code() {
  say "Getting the code into $TARGET_DIR"
  if [ -d "$TARGET_DIR" ]; then
    if [ -f "$TARGET_DIR/config.yaml" ]; then
      echo "ERROR: $TARGET_DIR/config.yaml exists." >&2
      echo "It would win over $CONFIG_FILE (CONFIG_PATHS order) and the" >&2
      echo "deployed config would be silently ignored. Remove it and re-run." >&2
      exit 1
    fi
    echo "  $TARGET_DIR already exists; leaving the checkout as it is."
    return
  fi

  local origin=""
  if [ -d "$SCRIPT_DIR/.git" ]; then
    origin="$(git -C "$SCRIPT_DIR" remote get-url origin)"
  elif [ -n "${DESCRIBER_REPO_URL:-}" ]; then
    origin="$DESCRIBER_REPO_URL"
  fi
  if [ -z "$origin" ]; then
    echo "ERROR: $SCRIPT_DIR is not a git checkout, so there is no origin to" >&2
    echo "clone from. Set DESCRIBER_REPO_URL and re-run." >&2
    exit 1
  fi

  git clone --quiet "$origin" "$TARGET_DIR"
  local ref="${DESCRIBER_REF:-}"
  if [ -z "$ref" ] && [ -d "$SCRIPT_DIR/.git" ]; then
    ref="$(git -C "$SCRIPT_DIR" rev-parse HEAD)"
  fi
  if [ -n "$ref" ]; then
    git -C "$TARGET_DIR" checkout --quiet "$ref"
  fi
}

build_app() {
  say "Creating the Python environment"
  cd "$TARGET_DIR"
  if [ ! -d .venv ]; then
    python3 -m venv .venv
  fi
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt

  say "Installing Piper"
  if [ ! -x "$TARGET_DIR/.piper/piper/piper" ]; then
    mkdir -p "$TARGET_DIR/.piper"
    curl -fsSL "$PIPER_URL" | tar -xz -C "$TARGET_DIR/.piper"
  fi
  ln -sf "$TARGET_DIR/.piper/piper/piper" /usr/local/bin/piper

  say "Fetching the voice: $VOICE"
  mkdir -p "$TARGET_DIR/voices"
  # Voice paths look like .../en_GB/alan/medium/en_GB-alan-medium.onnx
  local voice_rest="${VOICE#en_GB-}"
  local speaker="${voice_rest%%-*}"
  local quality="${voice_rest##*-}"
  local suffix target
  for suffix in onnx onnx.json; do
    target="$TARGET_DIR/voices/$VOICE.$suffix"
    if [ ! -f "$target" ]; then
      curl -fsSL -o "$target" "$VOICE_BASE/$speaker/$quality/$VOICE.$suffix"
    fi
  done

  chown -R describer:describer "$TARGET_DIR"
}

seed_config() {
  say "Preparing configuration"
  install -d -m 755 /etc/describer
  if [ ! -f "$CONFIG_FILE" ]; then
    cp "$TARGET_DIR/config.example.yaml" "$CONFIG_FILE"
    # RDM-only first run: an image ships with no shared RTT allowance to spend.
    "$TARGET_DIR/.venv/bin/python3" - "$CONFIG_FILE" <<'PY'
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
data.setdefault("sources", {})["fallback"] = None
with open(path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(data, fh, sort_keys=False)
PY
  fi
  chown describer:describer "$CONFIG_FILE"
  chmod 644 "$CONFIG_FILE"

  # Credentials never go near config.yaml. The directory is root's; the file,
  # once it exists, is describer's to rewrite in place (see credentials.py).
  if [ ! -f "$ENV_FILE" ]; then
    install -m 600 -o describer -g describer /dev/null "$ENV_FILE"
  fi
}

set_timezone() {
  # "Unset" means no /etc/localtime at all; a Pi that already has one chose it.
  if [ ! -e /etc/localtime ] && command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone Europe/London 2>/dev/null || true
  fi
}

install_cursor_theme() {
  # A blank cursor theme. cage parks a pointer in the middle of the screen even
  # with no mouse attached, and has no flag to hide it. It builds its cursor
  # manager with a null theme name and a hardcoded size, so XCURSOR_THEME and
  # XCURSOR_SIZE are both ignored: it loads whatever theme is named "default".
  # So we ship a theme *called* default, of transparent pixels, and put its
  # directory first on XCURSOR_PATH.
  say "Installing the blank cursor theme"
  local cursor_dir=/usr/local/share/describer-cursors
  # install -d, not mkdir -p: mkdir takes the caller's umask, and a umask of
  # 077 leaves a theme cage itself cannot read, which it ignores in silence.
  install -d -m 755 "$cursor_dir" "$cursor_dir/default" "$cursor_dir/default/cursors"
  python3 -c '
import struct, sys
# Xcursor: header, one TOC entry, one 24x24 fully transparent ARGB image.
size = 24
out = struct.pack("<4sIII", b"Xcur", 16, 0x00010000, 1)
out += struct.pack("<III", 0xfffd0002, size, 28)
out += struct.pack("<IIIIIIIII", 36, 0xfffd0002, size, 1, size, size, 0, 0, 0)
out += b"\x00" * (size * size * 4)
sys.stdout.buffer.write(out)
' | install -m 644 /dev/stdin "$cursor_dir/default/cursors/default"
  local name
  for name in left_ptr arrow top_left_arrow pointer hand1 hand2 xterm text watch; do
    ln -sf default "$cursor_dir/default/cursors/$name"
  done
  printf '[Icon Theme]\nName=default\n' | install -m 644 /dev/stdin "$cursor_dir/default/index.theme"
  ln -sfn default "$cursor_dir/describer-blank"
}

install_units() {
  say "Installing services"
  install -m 644 "$TARGET_DIR/deploy/describer.service" /etc/systemd/system/describer.service
  sed -e "s|@USER@|describer|g" -e "s|@UID@|$(id -u describer)|g" \
    "$TARGET_DIR/deploy/kiosk.service" \
    | install -m 644 /dev/stdin /etc/systemd/system/kiosk.service
  install -m 755 "$TARGET_DIR/deploy/shutdown_button.py" /usr/local/bin/shutdown-button
  install -m 644 "$TARGET_DIR/deploy/shutdown-button.service" \
    /etc/systemd/system/shutdown-button.service

  install -d -m 755 /etc/polkit-1/rules.d
  install -m 644 "$TARGET_DIR/deploy/polkit/50-describer.rules" \
    /etc/polkit-1/rules.d/50-describer.rules

  systemctl enable describer.service
  systemctl enable kiosk.service
  systemctl enable shutdown-button.service
  systemctl set-default graphical.target
}

migrate_old_layout() {
  local old_user="${SUDO_USER:-}"
  if [ -z "$old_user" ] || [ "$old_user" = "root" ]; then
    return
  fi
  local old_home
  old_home="$(getent passwd "$old_user" | cut -d: -f6)"
  if [ -z "$old_home" ]; then
    return
  fi
  local old_dir="$old_home/describer"
  local old_unit="$old_home/.config/systemd/user/describer.service"
  if [ ! -f "$old_unit" ]; then
    return
  fi

  say "Migrating from the old per-user install at $old_dir"

  if [ -f "$old_dir/config.yaml" ]; then
    if [ -f "$CONFIG_FILE" ]; then
      echo "  Both $old_dir/config.yaml and $CONFIG_FILE exist."
      echo "  $CONFIG_FILE wins now; the old one is left untouched."
    else
      cp "$old_dir/config.yaml" "$CONFIG_FILE"
      chown describer:describer "$CONFIG_FILE"
      chmod 644 "$CONFIG_FILE"
      echo "  Copied $old_dir/config.yaml -> $CONFIG_FILE"
    fi
  fi

  local name
  for name in voices .piper; do
    if [ -d "$old_dir/$name" ] && [ ! -e "$TARGET_DIR/$name" ]; then
      cp -a "$old_dir/$name" "$TARGET_DIR/$name"
      chown -R describer:describer "$TARGET_DIR/$name"
      echo "  Copied $old_dir/$name -> $TARGET_DIR/$name (saves the download)"
    fi
  done

  if [ -f "$ENV_FILE" ]; then
    chown describer:describer "$ENV_FILE"
  fi

  # Drop to the old user to reach their session bus; not chroot-safe, but an
  # old per-user layout cannot exist in the pi-gen chroot, so this never runs
  # there.
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$old_user" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$old_user")" \
      systemctl --user disable --now describer.service >/dev/null 2>&1 || true
  fi
  rm -f "$old_unit"

  echo "  $old_dir is left in place; delete it once the new board is confirmed working."
}

provision() {
  require_root provision
  install_packages
  create_user
  get_code
  build_app
  seed_config
  set_timezone
  install_cursor_theme
  install_units
  migrate_old_layout
  say "Provisioning done"
}

# ---------------------------------------------------------------------------
# configure: needs a real, running system and (usually) a terminal.
# ---------------------------------------------------------------------------

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
  # An if, not `[ ] && printf`: a blank answer would leave the function
  # returning 1, and set -e would end the install at "keep existing".
  if [ -n "$answer" ]; then
    printf -v "$1" '%s' "$answer"
  fi
}

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

configure() {
  require_root configure

  say "Credentials"
  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
  fi
  echo "Credentials (leave blank to keep what is already there):"
  ask RDM_API_KEY "Rail Data Marketplace API key" silent
  ask RTT_TOKEN "Realtime Trains token (api-portal.rtt.io)" silent

  umask 077
  cat > "$ENV_FILE" <<ENV
# Written by deploy/install.sh. Credentials only — never commit this file.
RDM_API_KEY=$(quote "${RDM_API_KEY:-}")
RTT_TOKEN=$(quote "${RTT_TOKEN:-}")
ENV
  chown describer:describer "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  say "Starting services"
  systemctl daemon-reload
  systemctl restart describer.service
  systemctl restart kiosk.service
  systemctl restart shutdown-button.service

  say "Done"
  local ip
  ip="$(hostname -I | awk '{print $1}')"
  cat <<MSG
Board:    http://$ip:8080/
Settings: http://$ip:8080/admin
Logs: journalctl -u describer -f   and   journalctl -u kiosk -f
Shutdown button: six presses in 10 s on GPIO21 (pin 40 to GND, pin 39)
                 journalctl -u shutdown-button -n 5
MSG
}

# ---------------------------------------------------------------------------

MODE="${1:-all}"
case "$MODE" in
  provision) provision ;;
  configure) configure ;;
  all)
    provision
    configure
    ;;
  *)
    echo "Usage: $0 [provision|configure|all]" >&2
    exit 1
    ;;
esac
