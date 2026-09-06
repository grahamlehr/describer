# Describer — UK Rail Departure Board for Raspberry Pi

## What this is

A live departure board for one or two UK National Rail stations, displayed
full-screen on a 16:9 monitor attached to a Raspberry Pi 4B. It pulls live
data from National Rail's Darwin feed, renders it in one of three visual
themes, and can speak platform-style announcements as trains approach.

Personal project for a single Pi. Optimise for simplicity and reliability
over portability or packaging. No multi-user, no auth beyond LAN trust.

## Hardware and OS

- Raspberry Pi 4B, Raspberry Pi OS 64-bit **Lite** (no desktop). Anything
  graphical must be installed and started by us.
- Display: 16:9 monitor on HDMI. Target 1920x1080; layout must also look
  right at 1280x720. Landscape only.
- Audio: HDMI audio by default; 3.5 mm jack must be selectable in config.
- Development happens on a Mac; deployment target is the Pi. Keep the app
  runnable on both (no Pi-only imports at module load time).

## Stack

- **Python 3.11+** backend using **FastAPI** + **uvicorn**.
- **Kiosk browser**: Chromium launched by a systemd user service in
  `--kiosk` mode against `http://localhost:8080/`. On Lite this needs a
  minimal Wayland/cage or X session; prefer `cage` (kiosk compositor) over
  a full desktop.
- **Frontend**: plain HTML/CSS/JS served by FastAPI. No build step, no
  frameworks, no npm. Themes are CSS + small JS modules. Live updates via
  Server-Sent Events from the backend so the page never polls the API
  itself.
- **TTS**: **Piper** running locally (British English voice, e.g.
  `en_GB-alan-medium` or `en_GB-southern_english_female`). Audio played via
  `aplay`/`paplay`. Generated clips cached on disk keyed by text hash.
- **Config**: YAML file at `config.yaml` (repo root in dev,
  `/etc/describer/config.yaml` on the Pi), plus a web admin page at
  `/admin` that reads/writes the same file. The file is the source of truth.
- Package management: `pip` with `requirements.txt`. Use a venv at `.venv`.
- Tests: `pytest`. Keep the data layer testable with recorded JSON fixtures.

## Data source: Rail Data Marketplace (Darwin LDBWS)

- Use the **Live Departure Board (LDBWS)** product from the Rail Data
  Marketplace (raildata.org.uk). It exposes Darwin data over a JSON REST
  API keyed by an `x-apikey` header. Do not use the old SOAP OpenLDBWS
  endpoint unless the REST one is unavailable.
- Endpoints we need (station identified by 3-letter CRS code):
  - `GetDepBoardWithDetails/{crs}` — departures with calling points.
  - `GetArrBoardWithDetails/{crs}` — arrivals (for arrivals mode).
- API key lives in the environment variable `RDM_API_KEY` or in a
  `.env` file that is **never committed**. Never write the key into
  `config.yaml` or logs.
- Poll interval default 30 s, configurable, never below 20 s. One request
  per configured station per poll. Back off exponentially on errors and
  keep showing the last good board with a "data stale" indicator.
- Normalise API responses into our own `Service` dataclass
  (scheduled time, estimated time, destination, origin, operator, platform,
  calling points, status flags: on time / delayed / cancelled / expected)
  so themes and announcements never touch raw API shapes.

## Features

### Boards
- One or two stations. Two stations render **side by side, split screen**,
  each half a complete board.
- Per-station mode: `departures` (default) or `arrivals`. A station may
  be listed twice to show both.
- Rows show: scheduled time, destination (or origin for arrivals),
  platform, expected time or status ("On time", "Exp 14:37", "Delayed",
  "Cancelled"), operator. Selected row (usually the first) expands to
  scroll its calling points.
- Configurable number of rows and clock display. Always show station name
  and a live clock.

### Themes (selectable in config, switchable live from `/admin`)
1. **modern** — clean, high-contrast, sans-serif, dark background,
   subtle colour for status. Default.
2. **crt** — retro terminal look: amber or green phosphor (configurable),
   scanlines, slight barrel distortion and bloom via CSS, monospace font,
   cursor blink. Must remain legible; effects are decoration not noise.
3. **splitflap** — Solari-style mechanical board. Each character is a
   flap that animates through the alphabet to its target; row changes
   flip, with an optional click sound. Fixed-width character grid, cream
   text on black flaps. Performance matters: keep animations
   GPU-composited (transform/opacity only) so the Pi 4 stays at 60 fps.

Themes share one DOM structure and one data model; a theme is a CSS file
plus an optional JS module for animation. Adding a theme must not require
touching backend code.

### Announcements (Piper TTS)
- Global on/off, per-station on/off, volume, voice, and lead time
  (default: announce when a service is ~2 minutes from expected time).
- Classic station phrasing, per train:
  "The next train to arrive at platform 2 will be the 14:32 Great Western
  Railway service to London Paddington, calling at Reading, Slough and
  London Paddington." Plus optional delay and cancellation announcements
  with the standard apology wording.
- Each service is announced at most once per event type (arriving,
  delayed, cancelled). Track announced IDs in memory; reset on restart.
- Optional two-tone chime before each announcement.
- Quiet hours honoured (see schedule below).

### Display schedule
- Configurable daily on/off times (e.g. off 23:00–06:00), per-weekday
  optional. When "off", blank the screen and put the HDMI output to sleep
  (`wlr-randr` under cage, or `vcgencmd display_power` fallback), stop
  polling and stop announcements. Wake on schedule.

### Admin page (`/admin`)
- Edit every config option with validation, save to `config.yaml`, apply
  live without restart where possible (theme, stations, announcements).
  Show current API status, last fetch time, and a "test announcement"
  button. LAN-only; no login.

## Project layout

```
describer/
  CLAUDE.md
  README.md
  requirements.txt
  config.example.yaml      # documented defaults; copy to config.yaml
  describer/
    __init__.py
    main.py                # FastAPI app, SSE endpoint, static + admin routes
    config.py              # load/validate/save YAML (pydantic models)
    rail/
      client.py            # RDM LDBWS HTTP client
      models.py            # Service, Board dataclasses
      poller.py            # background polling, staleness, backoff
    announce/
      phrasing.py          # builds announcement text from Service
      tts.py               # Piper wrapper + cache + playback
      scheduler.py         # decides what to announce and when
    schedule.py            # display on/off schedule
    web/
      static/
        index.html
        board.js           # SSE client, renders shared DOM
        themes/
          modern.css
          crt.css  crt.js
          splitflap.css  splitflap.js
        admin.html  admin.js
  deploy/
    install.sh             # Pi setup: apt deps, venv, piper, cage, services
    describer.service      # systemd unit for backend
    kiosk.service          # systemd unit for cage + chromium
  tests/
    fixtures/              # recorded LDBWS JSON responses
```

## Conventions

- Type hints everywhere; `pydantic` models for config and API payloads.
- Format with `ruff format`, lint with `ruff check`. Run both before
  finishing any change.
- Async I/O for the HTTP client and poller (`httpx.AsyncClient`).
- Log with the stdlib `logging` module to stdout; systemd captures it.
  Never log the API key or full raw responses at INFO.
- Frontend JS: ES modules, no bundler, no dependencies. Fonts are
  self-hosted in `web/static/fonts` so the board works with no internet.
- Keep the Pi in mind: avoid heavy Python deps (no pandas, no numpy),
  and avoid CSS filters that force full-screen repaints every frame.
- Every config option has a sensible default in `config.example.yaml`
  and a one-line comment explaining it.

## Running

Local dev (Mac):
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
export RDM_API_KEY=...
uvicorn describer.main:app --reload --port 8080
```
Then open `http://localhost:8080/` for the board, `/admin` for settings.

Pi: run `deploy/install.sh` once, then `systemctl --user status describer kiosk`.

## Out of scope for v1

Destination/calling-point filters, multi-Pi sync, mock data mode,
authentication, packaging for other users, portrait layout, non-UK data.
