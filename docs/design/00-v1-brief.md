> **History.** This is CLAUDE.md as it stood before the addenda were moved
> out, kept verbatim for the reasoning in it. Parts are superseded by the
> notes 01–15 that follow; the current state and rules are in `CLAUDE.md`.

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
- Shutdown button: a momentary switch from GPIO21 (pin 40) to GND (pin 39).
  Six presses within 10 s runs `systemctl poweroff`, so the Pi can be turned
  off without risking the SD card. `deploy/shutdown_button.py` is its own
  system service on the system Python with the apt `python3-gpiozero` and
  `python3-lgpio`, deliberately outside the app and its venv. Only one process
  can own the pin, so any further gesture on that button goes in that script.
  The press counting is the pure `Gesture` class, tested in
  `test_shutdown_button.py`; `gpiozero` is imported only in `main()`.
- Development happens on a Mac; deployment target is the Pi. Keep the app
  runnable on both (no Pi-only imports at module load time).

## Stack

- **Python 3.11+** backend using **FastAPI** + **uvicorn**.
- **Kiosk browser**: Chromium launched by a systemd **system** service bound
  to tty1, in `--kiosk` mode against `http://localhost:8080/`. On Lite this
  needs a minimal Wayland/cage or X session; prefer `cage` (kiosk compositor)
  over a full desktop. It cannot be a user service: cage needs a logind seat
  for DRM and input, and a lingering user session has none, so cage exits in
  a restart loop and the console keeps its login prompt. The binary on current
  Pi OS is `/usr/bin/chromium`; there is no `chromium-browser`. cage draws a
  pointer in the middle of the screen with or without a mouse and has no flag
  to hide it. It also ignores `XCURSOR_THEME` and `XCURSOR_SIZE` and loads
  whatever theme is named `default`, so `install.sh` writes a transparent
  theme under that name in `/usr/local/share/describer-cursors` and the unit
  puts that directory first on `XCURSOR_PATH`. The board's `cursor: none`
  alone is not enough, because with no input device no pointer ever enters
  the window.
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

> **Superseded in part.** RDM is now the *primary* of two sources. See
> the addendum at the end of this file for the second source (Realtime
> Trains), the source abstraction, and automatic failover. Where the two
> sections disagree, the addendum wins.

- Use the **Live Arrival and Departure Boards (LDBWS)** product from the Rail
  Data Marketplace (raildata.org.uk). It exposes Darwin data over a JSON REST
  API keyed by an `x-apikey` header. Do not use the old SOAP OpenLDBWS
  endpoint unless the REST one is unavailable.
- We subscribe to the combined **Live Arrival and Departure Boards** product
  and call one endpoint (station identified by 3-letter CRS code):
  - `GetArrDepBoardWithDetails/{crs}` — every service touching the station,
    with calling points. Departures read `std`/`etd` and
    `subsequentCallingPoints`; arrivals read `sta`/`eta` and
    `previousCallingPoints`, so both modes come from one response and one
    subscription. A service missing the mode's time (it terminates or
    originates here) is dropped by the parser.
  - The departures-only product (`…-dep1_2`) exposes
    `GetDepBoardWithDetails` and has no arrivals operation at all; switching
    back means changing `ldbws.BOARD_ENDPOINT` and `sources.rdm.base_url`
    together.
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
  show its calling points, paged every 5 s.
- Configurable number of rows and clock display. Always show station name
  and a live clock.
- Per-station `platforms` filter (empty = every platform), matched ignoring
  case and space. Both feeds withhold a platform until it is confirmed, so a
  filtered board runs short until then; `show_unplatformed` keeps those
  trains on it, at the cost of some that turn out to be another platform's.
  The filter is applied where boards are handed out rather than where they
  are fetched, so widening it from `/admin` needs no new API call, and
  announcements follow it.
- Per-station `walk_time` (minutes, 0 = off): a train whose effective time is
  nearer than that is dropped, because it cannot be reached from where the
  board is read and printing it pushes a catchable train off the bottom. It is
  measured against `Service.effective_time`, so a train running late comes
  *back* on to a filtered board. A service whose time will not parse is kept,
  and so is one the feed calls only "Delayed" (`Service.time_is_known`): its
  scheduled time has usually passed but the train has not gone, and nothing
  says when it goes. Applied beside the
  platform filter in `Poller.boards()`, so it is re-evaluated against the clock
  on every read rather than frozen into the cached board.
  **It moves the announcement lead with it.** A train leaves a walk-filtered
  board before it is ever within `announcements.lead_time`, so the arriving
  call would simply never come; the scheduler announces at
  `max(lead_time, walk_time)` instead — the last useful moment to call a train
  is the moment it stops being catchable.

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
4. **1990s** — a Ceefax page: forty columns of Bedstead on black, white on
   blue title bars, yellow times, and the clock written the Teletext way.
5. **nse** — a Network SouthEast platform indicator: a printed casing around
   a flip-dot matrix whose discs sweep column by column. A line too long for
   it turns a page at a time, because a disc cannot slide sideways.
6. **led-matrix** — the amber LED panel of the 2000s: one field edge to edge,
   so every word on it is lit dots, and a long line scrolls.
7. **thameslink** — the LCD "next train" panels on the Thameslink core: one
   service given the top of the screen with its route drawn down an amber
   line beneath it, then a blue-barred "Later trains" list counting down in
   minutes, and the clock in a white panel at the foot.

Themes share one DOM structure and one data model; a theme is a CSS file
plus an optional JS module for animation. Adding a theme touches no backend
code except its name in the `ThemeName` literal in `config.py`, so the config
validates (plus a `ThemesConfig` block if it has options). Addendum 3 carries the sizing model, the full module
contract and what the two dot-matrix themes share.

### Announcements (Piper TTS)
- Global on/off, per-station on/off, volume, voice, and lead time
  (default: announce when a service is ~2 minutes from expected time).
- Classic station phrasing, per train:
  "The next train to arrive at platform 2 will be the 14:32 Great Western
  Railway service to London Paddington, calling at Reading, Slough and
  London Paddington." Plus optional delay and cancellation announcements
  with the standard apology wording.
- Each service is announced at most once per event type (arriving,
  delayed, cancelled). Tracked in memory and reset on restart; keyed by the
  train rather than `Service.id` (Addendum 1) and forgotten by age
  (Addendum 6).
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
- The options are split across tabs (Stations, Display, Profiles, Data
  sources, Announcements, Schedule, Status), one panel visible at a time, with
  the current tab in the URL hash. Save, Discard and a live health chip sit in
  a sticky bar, because the fields no longer end anywhere near a button.
- **The form is `novalidate` on purpose.** A `required` field on a hidden tab
  cannot be focused, so the browser refuses to submit and reports nothing;
  `admin.js` finds the first invalid field itself, opens its tab, and calls
  `reportValidity()` there. Anything added to a panel must keep that path.
- Only the selected theme's options are rendered, and a switched-off
  announcements or schedule block is `inert` and dimmed rather than removed —
  `readForm` still reads it, so the values in the file survive the round trip.

## Project layout

```
describer/
  CLAUDE.md
  README.md
  requirements.txt         # runtime and dev deps together
  pyproject.toml           # ruff and pytest settings
  .claude/launch.json      # the dev server for preview tools; clears RTT_TOKEN (Addendum 4)
  config.example.yaml      # documented defaults; copy to config.yaml
  .env.example             # the credential names; .env itself is never committed
  describer/
    __init__.py
    main.py                # FastAPI app, SSE endpoint, static + admin routes
    config.py              # load/validate/save YAML (pydantic models)
    schedule.py            # display on/off schedule
    profiles.py            # which profile is in force, and merging it in
    stationlist.py         # builds web/static/stations.json from NaPTAN; run by hand
    credentials.py         # write-only API keys from /admin (Addendum 13)
    updater.py             # checks GitHub for a newer release, installs on request (Addendum 13)
    weather.py             # Open-Meteo forecasts: cache, refresh loop (Addendum 15)
    rail/
      base.py              # RailSource protocol, RailApiError
      models.py            # Service, Board dataclasses
      ldbws.py             # Rail Data Marketplace (Darwin) client
      rtt.py               # Realtime Trains client
      sources.py           # holds both clients, fails over, recovers
      poller.py            # background polling, staleness, backoff
      client.py            # deprecated alias for ldbws; delete after a release
    announce/
      phrasing.py          # builds announcement text from Service
      tts.py               # Piper wrapper + cache + playback
      scheduler.py         # decides what to announce and when
    web/
      static/
        index.html         # the one DOM every theme fills
        base.css           # structure and the sizing model; themes add the look
        board.js           # SSE client, renders the DOM, calls into the theme
        admin.html  admin.css  admin.js
        stations.json      # every station's CRS and name, for /admin's lookup (Addendum 10)
        fonts/             # self-hosted; bedstead.woff2 ships with the repo
        themes/
          modern.css      modern.js
          crt.css         crt.js
          splitflap.css   splitflap.js
          1990s.css       1990s.js
          nse.css         nse.js
          led-matrix.css  led-matrix.js
          thameslink.css  thameslink.js
          dotmatrix.js    # the shared 5x7 dot font; not a theme
          colours.js      # writes the Addendum 5 palette; not a theme
  deploy/
    install.sh             # Pi setup: apt deps, venv, piper, cage, services
    describer.service      # systemd *user* unit for the backend
    kiosk.service          # systemd *system* unit for cage + chromium
    shutdown_button.py     # six presses on GPIO21 in 10 s -> poweroff; system python
    shutdown-button.service  # systemd *system* unit for the above
  tests/
    conftest.py            # autouse guards: no credentials, no network, no update checks
    fixtures/              # recorded LDBWS, RTT, NaPTAN and Open-Meteo responses
```

## Conventions

- Type hints everywhere; `pydantic` models for config and API payloads.
- Format with `ruff format`, lint with `ruff check`. Run both before
  finishing any change.
- Async I/O for the HTTP client and poller (`httpx.AsyncClient`).
- Log with the stdlib `logging` module to stdout; systemd captures it.
  Never log the API key or full raw responses at INFO.
- Frontend JS: ES modules, no bundler, no dependencies. Any web font is
  self-hosted in `web/static/fonts`, never a CDN, so the board works with no
  internet. Only `bedstead.woff2` ships; every other theme falls back to
  system fonts (see `fonts/README.md`).
- Keep the Pi in mind: avoid heavy Python deps (no pandas, no numpy),
  and avoid CSS filters that force full-screen repaints every frame.
- Every config option has a sensible default in `config.example.yaml`
  and a one-line comment explaining it.
- `/static` and both pages answer with `Cache-Control: no-cache`. The kiosk is
  never hard-refreshed, so nothing may sit in its cache without being checked
  with us first; the ETag makes that a 304.

## Running

Local dev (Mac):
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
export RDM_API_KEY=...
unset RTT_TOKEN            # dev never talks to RTT; see Addendum 4
uvicorn describer.main:app --reload --port 8080
```
Set `sources.fallback: null` in the local `config.yaml`. The `describer`
entry in `.claude/launch.json` (what `preview_start` runs) starts uvicorn with
`RTT_TOKEN` set to empty, so a token in the local `.env` is never loaded. RDM has a generous
allowance and is the only upstream a dev machine may call; RTT's free tier is
1000 calls a day shared with the live board, so **anything spent here is taken
off the Pi**. Addendum 4 is the rule, not a suggestion.

Then open `http://localhost:8080/` for the board, `/admin` for settings.

Pi: run `deploy/install.sh` once, then `systemctl --user status describer`
and `sudo systemctl status kiosk shutdown-button`.

`install.sh` runs under `set -e`, so a helper function must not end on a
`[ … ] && …` list: a false test becomes the function's status and ends the
install. That is how pressing Enter to keep a credential used to abort it.
Use an `if`.

## Out of scope for v1

Destination/calling-point filters (the per-platform filter above is in),
multi-Pi sync, mock data mode, authentication, packaging for other users,
portrait layout, non-UK data.

