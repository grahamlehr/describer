# Hardware, OS and stack

_Moved from the old CLAUDE.md ("Hardware and OS" and "Stack"). Rules summarised in `AGENTS.md`._

- Raspberry Pi 4B, Raspberry Pi OS 64-bit **Lite** (no desktop). Anything
  graphical must be installed and started by us.
- Display: 16:9 monitor on HDMI. Target 1920x1080; layout must also look
  right at 1280x720. Landscape only.
- Audio: HDMI audio by default; 3.5 mm jack must be selectable in config.
- Shutdown button: a momentary switch from GPIO21 (pin 40) to GND (pin 39).
  Six presses within 10 s runs `systemctl poweroff`, so the Pi can be turned
  off without risking the SD card. `deploy/shutdown_button.py` is its own
  system service on the system Python with the apt `python3-gpiozero` and
  `python3-lgpio`, deliberately outside the app and its venv. Only one process
  can own the pin, so any further gesture on that button goes in that script.
  The press counting is the pure `Gesture` class, tested in
  `test_shutdown_button.py`; `gpiozero` is imported only in `main()`.
  `/admin` → Status → Power has **Shut down** and **Restart** too
  (`POST /api/system/poweroff|reboot`, `describer/system.py`): the backend runs
  `systemctl poweroff|reboot` as `describer`, allowed by polkit. They answer
  409 "Only on the Pi" without `/etc/describer`, and `Power`'s command is
  injectable: `conftest.py` replaces the real one with a tripwire, so a test
  can never power off the machine it runs on. The button stays as it is; it
  works when the network does not.
- Development happens on a Mac; deployment target is the Pi. Keep the app
  runnable on both (no Pi-only imports at module load time).
- Deploying to the Pi is the user's job: there is no SSH key from the dev
  Mac. `/api/status` over HTTP still says what is running there.

## Stack

- **Python 3.11+** backend using **FastAPI** + **uvicorn**.
- **One system layout.** The checkout lives at `/opt/describer`, owned by a
  dedicated `describer` system user (`useradd --system`, no login shell,
  home `/var/lib/describer`, groups `video,render,input,audio,gpio`).
  `describer.service` and `kiosk.service` are both systemd **system** units
  running as `describer` — there is no user-scope unit anywhere in this
  layout, and no `loginctl enable-linger`. `deploy/install.sh` is root-run
  (`sudo deploy/install.sh [provision|configure|all]`) and builds this from
  scratch or migrates an older per-user install onto it.
- **Kiosk browser**: Chromium launched by a systemd **system** service bound
  to tty1, in `--kiosk` mode against `http://localhost:8080/`, under `cage`
  (a kiosk Wayland compositor) rather than a desktop. It cannot be a user
  service: cage needs a logind seat for DRM and input, and a lingering user
  session has none, so cage exits in a restart loop and the console keeps its
  login prompt. The binary on current Pi OS is `/usr/bin/chromium`; there is
  no `chromium-browser`. cage draws a pointer in the middle of the screen with
  or without a mouse and has no flag to hide it. It also ignores
  `XCURSOR_THEME` and `XCURSOR_SIZE` and loads whatever theme is named
  `default`, so `install.sh` writes a transparent theme under that name in
  `/usr/local/share/describer-cursors` and the unit puts that directory first
  on `XCURSOR_PATH`. The board's `cursor: none` alone is not enough, because
  with no input device no pointer ever enters the window.
- **Frontend**: plain HTML/CSS/JS served by FastAPI. No build step, no
  frameworks, no npm. Themes are CSS + small JS modules. Live updates via
  Server-Sent Events from the backend so the page never polls the API
  itself.
- **TTS**: **Piper** running locally (British English voice, e.g.
  `en_GB-alan-medium`). Audio played via `aplay`/`paplay` (`afplay` in dev).
  Generated clips cached on disk keyed by text hash.
- **Config**: YAML file at `config.yaml` (repo root in dev,
  `/etc/describer/config.yaml` on the Pi; a `config.yaml` in the checkout wins,
  and `DESCRIBER_CONFIG` overrides both), plus a web admin page at `/admin`
  that reads/writes the same file. The file is the source of truth. On the
  Pi, `/etc/describer` is root-owned (755); `install.sh` seeds
  `config.yaml` and `describer.env` there and hands each to `describer`
  (644 and 600 respectively), and `install.sh` refuses to provision if
  `/opt/describer/config.yaml` exists, because that would win instead.
- Package management: `pip` with `requirements.txt`. Use a venv at `.venv`.
- Tests: `pytest`. Keep the data layer testable with recorded JSON fixtures.
