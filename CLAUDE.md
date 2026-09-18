# Describer — UK Rail Departure Board for Raspberry Pi

## What this is

A live departure board for one or two UK National Rail stations, displayed
full-screen on a 16:9 monitor attached to a Raspberry Pi 4B. It pulls live
data from National Rail's Darwin feed (falling back to Realtime Trains),
renders it in one of seven visual themes, and can speak platform-style
announcements as trains approach.

Personal project for a single Pi. Optimise for simplicity and reliability
over portability or packaging. No multi-user, no auth beyond LAN trust.

This file is the current state and the rules. **The reasoning behind each
feature lives in `docs/design/`**, one note per addendum, indexed at the end
of this file. Read the note for an area before changing it; the traps
recorded there cost real time to find.

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

## Data sources

Two independent upstreams behind one `RailSource` protocol
(`fetch_board(crs, mode) -> Board`). Each client has a pure
`parse_board(...)` that works on recorded JSON plus a thin async HTTP wrapper,
and both normalise into our own `Service` / `Board` dataclasses, so themes and
announcements never touch raw API shapes.

- **RDM (primary)**: Rail Data Marketplace **Live Arrival and Departure
  Boards** (Darwin LDBWS) over JSON REST, `x-apikey` header from
  `RDM_API_KEY`. One endpoint, `GetArrDepBoardWithDetails/{crs}`, serves both
  modes: departures read `std`/`etd` and `subsequentCallingPoints`, arrivals
  `sta`/`eta` and `previousCallingPoints`. The departures-only product has no
  arrivals operation; switching means changing `ldbws.BOARD_ENDPOINT` and
  `sources.rdm.base_url` together. Only RDM carries position and formation.
- **RTT (fallback)**: Realtime Trains next-generation API at
  `https://data.rtt.io`, bearer token from `RTT_TOKEN` (an access token or a
  refresh token; the client works out which). **Free tier: 10 calls a minute,
  100 an hour, 1000 a day, shared with the live Pi.** See the allowance rule
  below. (`docs/design/02`)
- **Failover** (`rail/sources.py`): the active source is global, so both
  halves of a split screen agree. After `failover_after` *consecutive*
  primary failures it switches to the fallback, and probes the primary every
  `recover_after` s; a primary success resets the count. Missing credentials
  are a permanent failure reported once. Failover never masks a stale board.
  `Board.source` says which feed served it. (`docs/design/01`, `/08`)
- Poll interval default 30 s, never below 20 s; while RTT is live, never below
  `sources.rtt.min_poll_interval` (120 s). Back off exponentially on errors and
  keep showing the last good board with a "data stale" indicator.
- Keys live in the environment or a **never-committed** `.env`. Never write a
  key into `config.yaml` or logs. `/admin` can set them, write-only
  (`docs/design/13`).

## Features

### Boards
- One or two stations. Two stations render **side by side, split screen**,
  each half a complete board.
- Per-station mode: `departures` (default) or `arrivals`. A station may
  be listed twice to show both.
- Rows show: scheduled time, destination (or origin for arrivals),
  platform, expected time or status ("On time", "Exp 14:37", "Delayed",
  "Cancelled"), operator. The top service expands to show its calling
  points, paged every 5 s, and the reason it is late or cancelled.
- Per-station `platforms` filter (empty = every platform), matched ignoring
  case and space. Both feeds withhold a platform until it is confirmed, so a
  filtered board runs short until then; `show_unplatformed` keeps those
  trains on it. The filter is applied where boards are handed out
  (`Poller.boards()`) rather than where they are fetched, so widening it needs
  no new API call, and announcements follow it.
- Per-station `walk_time` (minutes, 0 = off) drops a train whose
  `Service.effective_time` is nearer than that. A late train comes *back* on;
  a service whose time will not parse, or that the feed calls only "Delayed"
  (`Service.time_is_known`), is kept. Re-evaluated on every read beside the
  platform filter. **It moves the announcement lead with it**: the scheduler
  announces at `max(lead_time, walk_time)`, or the call would never come.
- Optional extras on the top service, `modern` and `thameslink` only: where
  the train is now and its formation (`docs/design/07`, `/08`). Each calling
  point's time works on every theme (`/12`). thameslink can show the whole
  journey and scroll it (`/08`, `/09`). A 24-hour weather strip, `modern` and
  `thameslink` only (`/15`).

### Themes (selectable in config, switchable live from `/admin`)
1. **modern**: clean, high-contrast, sans-serif, dark. Default.
2. **crt**: amber or green phosphor, scanlines, barrel distortion and bloom,
   monospace, cursor blink. Must remain legible.
3. **splitflap**: Solari board; each character flips through the drum to its
   target, drawn on canvases from a tile sheet (`docs/design/14`).
4. **1990s**: a Ceefax page, forty columns of Bedstead, white on blue bars.
5. **nse**: Network SouthEast flip-dot indicator; discs sweep column by
   column and long lines page.
6. **led-matrix**: amber LED panel; everything is lit dots, long lines scroll.
7. **thameslink**: the Thameslink core LCD panels, one featured train with
   its route drawn beneath, then a "Later trains" list counting down.

Themes share one DOM structure and one data model; a theme is a CSS file
plus an optional JS module. Adding a theme touches no backend code except its
name in the `ThemeName` literal in `config.py` (plus a `ThemesConfig` block if
it has options). The sizing model, the full module contract and the per-theme
specifics are in `docs/design/03`; the palette roles in `/05` and `/11`.

### Announcements (Piper TTS)
- Global on/off, per-station on/off, volume, voice, lead time (default 120 s),
  optional two-tone chime, optional delay and cancellation announcements with
  the standard apology wording. Classic phrasing: "The next train to arrive
  at platform 2 will be the 14:32 Great Western Railway service to London
  Paddington, calling at Reading, Slough and London Paddington."
- Each service is announced at most once per event type (arriving, delayed,
  cancelled), in memory, reset on restart. Keyed by
  `(crs, mode, scheduled_time, destination)` so a source switch cannot
  announce a train twice, and forgotten by age (`FORGET_AFTER`, 30 min), not
  when the train leaves the board.

### Display schedule and profiles
- `schedule:` sets daily on/off times, optionally per weekday. When off, blank
  the screen and sleep HDMI (`wlr-randr` under cage, `vcgencmd` fallback),
  stop polling, weather and announcements.
- `profiles:` swap stations, display and announcements by time of day
  (`docs/design/06`). First match wins; no match is the base config.
  **The schedule wins**: while the screen is off no profile is active.
  `ConfigStore.get()` is the raw file (what `/api/config` and `/admin` use);
  `ConfigStore.active()` is the resolved config (what the poller, announcer,
  weather and `/api/status` use). Mixing the two up writes a profile into the
  base file.

### Admin page (`/admin`)
- Edits every config option with validation, saves to `config.yaml`, applies
  live without restart where possible. Shows API status, rate limit, last
  fetch, a test announcement button, Force source / Force profile (memory
  only), credentials (write-only) and updates from GitHub. LAN-only; no login.
- Tabs: Stations, Display, Profiles, Data sources, Announcements, Schedule,
  Status; one visible at a time, current tab in the URL hash. Save, Discard
  and a live health chip sit in a sticky bar.
- **The form is `novalidate` on purpose.** A `required` field on a hidden tab
  cannot be focused, so the browser refuses to submit and reports nothing;
  `admin.js` finds the first invalid field itself, opens its tab, and calls
  `reportValidity()` there. Anything added to a panel must keep that path.
- Only the selected theme's options are rendered, and a switched-off
  announcements or schedule block is `inert` and dimmed rather than removed;
  `readForm` still reads it, so the values in the file survive the round trip.
- Station boxes look names up in the committed `stations.json` (NaPTAN,
  rebuilt by hand with `python -m describer.stationlist`; `docs/design/10`).

## Project layout

```
describer/
  CLAUDE.md                # this file: current state and rules
  README.md                # for a person installing and running it
  requirements.txt         # runtime and dev deps together
  pyproject.toml           # ruff and pytest settings
  config.example.yaml      # documented defaults; copy to config.yaml
  .env.example             # the credential names; .env itself is never committed
  .claude/launch.json      # the dev server for preview tools; clears RTT_TOKEN
  docs/design/             # one note per addendum: why things are the way they are
  describer/
    main.py                # FastAPI app, SSE endpoint, static + admin routes
    config.py              # load/validate/save YAML (pydantic models)
    schedule.py            # display on/off schedule
    profiles.py            # which profile is in force, and merging it in
    stationlist.py         # builds web/static/stations.json from NaPTAN; run by hand
    credentials.py         # write-only API keys from /admin
    updater.py             # checks GitHub for a newer release, installs on request
    weather.py             # Open-Meteo forecasts: cache, refresh loop
    rail/
      base.py              # RailSource protocol, RailApiError
      models.py            # Service, Board, CallingPoint, Position, Formation
      ldbws.py             # Rail Data Marketplace (Darwin) client
      rtt.py               # Realtime Trains client
      sources.py           # holds both clients, fails over, recovers
      poller.py            # background polling, staleness, backoff, filters
    announce/
      phrasing.py          # builds announcement text from Service
      tts.py               # Piper wrapper + cache + playback
      scheduler.py         # decides what to announce and when
    web/static/
      index.html           # the one DOM every theme fills
      base.css             # structure and the sizing model; themes add the look
      board.js             # SSE client, renders the DOM, calls into the theme
      admin.html  admin.css  admin.js
      stations.json        # every station's CRS, name and coordinates
      fonts/               # self-hosted; only bedstead.woff2 ships
      themes/              # <theme>.css + <theme>.js for each of the seven
        dotmatrix.js       # the shared 5x7 dot font; not a theme
        colours.js         # writes the configured palette; not a theme
  deploy/
    install.sh             # root-run, two-part: provision (chroot-safe), configure
    describer.service      # systemd *system* unit for the backend, User=describer
    kiosk.service          # systemd *system* unit for cage + chromium, User=describer
    polkit/
      50-describer.rules   # lets describer restart describer.service, nothing else
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
  Never log an API key or full raw responses at INFO.
- Frontend JS: ES modules, no bundler, no dependencies. Any web font is
  self-hosted in `web/static/fonts`, never a CDN, so the board works with no
  internet; the others fall back to system fonts (`fonts/README.md`).
- Keep the Pi in mind: avoid heavy Python deps (no pandas, no numpy),
  and avoid CSS filters that force full-screen repaints every frame. Animate
  transform/opacity only, or draw on a canvas.
- Every config option has a sensible default in `config.example.yaml`
  and a one-line comment explaining it.
- `/static` and both pages answer with `Cache-Control: no-cache`. The kiosk is
  never hard-refreshed, so nothing may sit in its cache without being checked
  with us first; the ETag makes that a 304.
- `sources`, `schedule`, `updates`, `weather` and `display.resolution` are
  plumbing: never profile-overridable.
- A feature lands with its docs: `config.example.yaml`, README (user-facing),
  this file (if it changes the current state or adds a rule) and a new note in
  `docs/design/` (the why, what was measured, what was verified by hand).

## Never spend the RTT allowance on local work

The full rule is in `docs/design/04`. It is not optional.

- **Local runs and tests never call `data.rtt.io`.** Every RTT behaviour is
  exercisable from `tests/fixtures/rtt_*.json`; the live shape is in
  `rtt_live_capture.json`.
- Keep `RTT_TOKEN` unset in the dev shell. The local `.env` on this Mac *does*
  hold a real token; `.claude/launch.json` starts uvicorn with `RTT_TOKEN=`
  so python-dotenv (`override=False`) leaves it unloaded. Start a dev server
  through `preview_start`, or `env RTT_TOKEN= …` by hand, never bare.
- `sources.fallback: null` in the local `config.yaml`. Never use Force source,
  `/api/source/force`, or `sources.primary: rtt` on a dev machine.
- Never leave a dev server polling unattended; stop it when the check is done.
- `pytest` makes no network calls at all. `conftest.py` deletes the
  credentials for every test (opt in to the fake `rdm_credentials` /
  `rtt_credentials` fixtures), poisons the weather client's transport and
  keeps the updater from fetching. Add any new credential or upstream there.
- Recording a new fixture is the one legitimate live call: ask the user first,
  say how many calls it costs, capture once, commit it.

## Traps

Each of these has broken the board at least once. The design note named has
the full story.

- **`hidden` is beaten by an author `display`.** Any rule that sets `display`
  on an element `board.js` hides needs `[hidden] { display: none; }` beside
  it. (`03`)
- **Every child of `.board` names its own `grid-row`.** `.stale` is
  `display: none` most of the day; with auto-placement `.rows` fell into an
  `auto` track, its size container resolved to zero height and the board
  rendered blank. (`03`)
- **A row must be in the tree before its cells are painted.** A detached
  element has no computed style, so `--chars` reads empty. `setText`
  short-circuits on `dataset.rendered`, so a cell painted wrong stays wrong.
  (`03`)
- **Size from the configured row count, not the services in hand**, so the
  type holds still as trains drop off. (`03`)
- **A theme that writes its own `--slot` must add `--reason-share`** (and
  `--detail-share` / `--weather-share` if it opts in to those). thameslink
  deliberately does not. (`03`, `07`, `15`)
- **The nse watchdog must not be re-armed while one is pending**, or it never
  fires and the board freezes part-swept. (`03`)
- **thameslink: measure the stops against `.calling-points`, never the
  track**, and observe the block with a `ResizeObserver`. (`03`)
- **`glide` reads the transform before setting `transition: none`**, or the
  route leaps to the foot mid-scroll. (`09`)
- **Profile fields stay out of `readForm`'s sweep**; so do the station search
  box and the credential fields (no `name`, events stopped at the field).
  (`06`, `10`, `13`)
- **The poller takes one `active()` snapshot per tick** and hands that same
  object on; the announcer indexes stations against the boards it was given.
  On a profile switch, prune `_boards` of slots not in the new set, or a
  three-hour-old board renders as live. (`06`)
- **An empty assignment still assigns.** A bare `RDM_API_KEY=` in the
  checkout's `.env` once shadowed the deployed key on a per-user install and
  spent a day's RTT allowance — `describer.service` read the checkout `.env`
  first, and systemd applies environment files in order with the last
  assignment winning, so a blank line there beat a real key. The system unit
  now has only one `EnvironmentFile`, `/etc/describer/describer.env`, which
  removes the whole class of trap rather than just reordering it; the
  history is why `credentials.py` still clears a key by deleting its line
  instead of ever writing it empty. (`01`, `13`)
- **`install.sh` runs under `set -e`**: a helper must not end on a
  `[ … ] && …` list, or a false test ends the install. Use an `if`.
- **Unit files are copied, not pulled.** Changing `deploy/` means re-running
  `install.sh` (or re-copying) plus `daemon-reload`; the updater only says so.
- **An SSE stream never ends on its own.** Before a restart,
  `Poller.close_streams()` must run or shutdown waits for systemd to kill it.
  (`13`)
- **`requestAnimationFrame` does not run in a hidden browser pane** and
  `setInterval` is throttled in a background tab: splitflap freezes
  mid-alphabet and page turns stop. Keep the pane displayed when checking
  animation. (`03`)

## Values that live in two places

Change every home, or they drift silently.

| Value | Homes |
|-------|-------|
| nse / led-matrix character pitch | `PITCH_EM` (0.1) in the JS; `0.6em` per character in the CSS |
| splitflap tile pitch | `TILE_EM` / `GAP_EM` in `splitflap.js`; `0.86em` / `0.08em` / `1.32em` in `splitflap.css` |
| `--calling-share` (0.9) | `base.css`; set per board in `board.js` |
| profile time windows | `in_window` in `profiles.py`; `coversMinute` in `admin.js` |
| theme options | the Display tab's HTML; `THEME_OPTIONS` in `admin.js` |
| a stop's time rules | `stopTime` in `board.js`; its copy in `thameslink.js` |
| the theme names | `ThemeName` in `config.py`; the radios in `admin.html`; the list in `admin.js` |

## Running

Local dev (Mac):
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml     # then set sources.fallback: null
export RDM_API_KEY=...
env RTT_TOKEN= uvicorn describer.main:app --reload --port 8080
```
From a Claude session, use `preview_start` with the `describer` entry, which
does the same. Board at `http://localhost:8080/`, settings at `/admin`.

Before finishing:
```bash
env -u RTT_TOKEN -u RDM_API_KEY .venv/bin/python -m pytest -q
.venv/bin/ruff format . && .venv/bin/ruff check .
```

Pi: run `sudo deploy/install.sh` once, then `sudo systemctl status describer
kiosk shutdown-button`.

## Verifying a board change by hand

There is no frontend test harness. Check any change to what the board draws
in the browser pane, and measure it rather than eyeballing it:

- 1920×1080 and 1280×720, one board and two, every theme the change touches.
  Check that unaffected themes render exactly as before by comparing the
  computed `--slot` and row height.
- The names that break layouts, not the fixtures' short ones:
  `London Charing Cross`, `Ashford International`,
  `Abbey Wood via Whitechapel`.
- Truncation:
  `[...document.querySelectorAll(".cell")].filter(c => c.scrollWidth - c.clientWidth > 1)`.
  Not for splitflap: compare each canvas's right edge with its cell's, and
  `canvas.__chars` with the field's `--chars`.
- A stale board, and an RTT board. An RTT board has no position or
  formation, and no calling points when the allowance guard fired; that is
  not a rendering fault.
- If a theme looks like the previous release, the browser cached a file:
  re-fetch with `{cache: 'reload'}`.

## Design notes

`docs/design/NN-*.md`, one per addendum, in the order they were written. Each
records what was decided, what was measured, and what was verified. Where an
older note and a newer one disagree, the newer one wins, and this file wins
over both.

| # | Note | Covers |
|---|------|--------|
| 00 | `v1-brief` | The original brief, before any addendum |
| 01 | `rtt-failover` | Source abstraction, `SourceManager`, failover, status fields (its RTT API half is superseded by 02) |
| 02 | `rtt-next-generation` | RTT v2 API, token exchange, status mapping, rate-limit guard |
| 03 | `board-layout-and-theme-contract` | Sizing model, **theme module contract**, per-theme specifics, reasons, checklist |
| 04 | `rtt-allowance` | Never spend RTT calls locally; tests offline; recording fixtures |
| 05 | `palette` | The seven colour roles, `colours.js`, admin swatches and contrast |
| 06 | `profiles` | Time-of-day profiles, `ConfigStore.active()`, poller switching, admin editor |
| 07 | `position-and-formation` | Where the train is, coaches and loading, from LDBWS |
| 08 | `journey-and-formation` | Full journey on thameslink, car strip, consecutive-failure failover fix |
| 09 | `route-scrolling` | thameslink `scroll_route` |
| 10 | `station-lookup` | `stations.json` from NaPTAN, the admin combobox |
| 11 | `bar-colour-role` | thameslink's `bar` colour role |
| 12 | `calling-point-times` | `show_calling_times` and the time rules |
| 13 | `credentials-and-updates` | Write-only keys from /admin; updates from GitHub |
| 14 | `splitflap-canvas` | Flaps blitted from a tile sheet; the measurements |
| 15 | `weather` | Open-Meteo strip on modern and thameslink |

A new feature gets the next number. Write it the way the others are: the
decision, the reason, the numbers measured, the trap found, and how it was
checked.

## Out of scope

Destination/calling-point filters (the per-platform filter is in), multi-Pi
sync, mock data mode, authentication, packaging for other users, portrait
layout, non-UK data, a third data source, merging both sources at once.
