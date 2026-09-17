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
  to hide it, so `install.sh` writes a transparent cursor theme and the unit
  points `XCURSOR_THEME` at it; the board's `cursor: none` alone is not
  enough, because with no input device no pointer ever enters the window.
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
plus an optional JS module for animation. Adding a theme must not require
touching backend code. Addendum 3 carries the sizing model, the full module
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
  requirements.txt
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
    fixtures/              # recorded LDBWS and RTT JSON responses
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
Set `sources.fallback: null` in the local `config.yaml`. RDM has a generous
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

---

# Addendum 1 — Second data source: Realtime Trains (RTT) with failover

Added after v1 shipped on RDM only. Goal: a genuinely independent upstream
so the board keeps working when the Rail Data Marketplace is down or the
key expires. RDM stays primary; RTT is the fallback. Nothing in the
themes, announcements or SSE contract changes shape except one new field.

## Realtime Trains API facts

> **Superseded.** This section originally described the v1 API at
> `api.rtt.io` (HTTP Basic auth with a username and password). That portal is
> closed to new registrations and is being switched off; new accounts get the
> next-generation API described below. See "Addendum 2".

## Source abstraction

Introduce a small protocol so the poller does not know which API it is
talking to:

```
describer/rail/
  models.py        # unchanged, plus Board.source (see below)
  base.py          # RailSource Protocol, RailApiError (moved from client.py)
  ldbws.py         # LdbwsClient — the existing client.py, renamed
  rtt.py           # RttClient
  sources.py       # SourceManager: builds clients from config, does failover
  client.py        # thin re-export of ldbws for backwards compatibility; delete
                   # once nothing imports it
```

```python
class RailSource(Protocol):
    name: str  # "rdm" | "rtt"

    async def fetch_board(self, crs: str, mode: str) -> Board: ...
    async def aclose(self) -> None: ...
```

Both clients keep the split used by `ldbws.py`: a pure `parse_board(...)`
that works on recorded JSON, and a thin async HTTP wrapper. `Board` and
`Service` are the only things that leave the `rail` package.

### RttClient specifics

- `fetch_board` does one search call, then detail calls for the first
  `rows` services only (the board never shows more, and each detail call
  is a round trip). Detail results are cached in memory keyed by
  `(serviceUid, runDate)` for 10 minutes; calling points rarely change.
- Time fields: convert "1432" to "14:32" at the parser boundary. Downstream
  code assumes "HH:MM" everywhere.
- Status mapping into `ServiceStatus`:
  - `displayAs` starts with `CANCELLED_` or `cancelReasonLongText` set →
    `CANCELLED`.
  - `realtime*` present and equal to booked → `ON_TIME`.
  - `realtime*` present and later than booked → `EXPECTED`, with
    `delay_minutes` computed exactly as the LDBWS parser does.
  - `realtime*` absent → `UNKNOWN` (RTT has no bare "Delayed" state).
- Skip services where `isPassenger` is false. Skip `serviceType != "train"`
  unless `sources.rtt.include_buses` is true.
- `Service.id` is `f"rtt:{serviceUid}:{runDate}"`; LDBWS ids keep their
  Darwin `serviceID`. See the announcement note below for why this matters.
- `operator` = `atocName`, `operator_code` = `atocCode`.
  `destination` = joined `description`s of `locationDetail.destination`.
  `platform` = `platform` only when `platformConfirmed` is true, else
  `None` (LDBWS already omits unconfirmed platforms; keep parity).

## Failover behaviour (SourceManager)

- Config names a `primary` and an optional `fallback`. Each station slot
  is fetched from the active source; the active source is global, not
  per slot, so both halves of a split screen always agree.
- After `failover_after` consecutive failures of the primary (default 3,
  counted across all slots), switch to the fallback and log at WARNING.
  Poll the primary quietly in the background every `recover_after`
  seconds (default 300); on the first success switch back and log INFO.
- Fallback failures use the existing exponential backoff; the board goes
  stale exactly as it does today. Failover never masks a stale board.
- Missing credentials for a source count as a permanent failure for that
  source, reported once at startup, not retried every tick.
- `Board.source: str` (new field, `"rdm"` or `"rtt"`) is set by the
  manager on every board so the UI and status endpoint can show it.

## Config changes

`api:` is renamed `sources:`. The loader accepts the old `api:` key for one
release and maps it to `sources.rdm` with a deprecation warning.

```yaml
sources:
  primary: rdm              # rdm | rtt
  fallback: rtt             # rdm | rtt | null (null = no failover)
  failover_after: 3         # consecutive primary failures before switching
  recover_after: 300        # seconds between background retries of the primary
  poll_interval: 30         # unchanged, now applies to whichever source is live
  stale_after: 120          # unchanged
  rdm:
    base_url: https://api1.raildata.org.uk/1010-live-arrival-and-departure-boards-arr-and-dep1_1/LDBWS/api/20220120
    timeout: 10.0
  rtt:
    base_url: https://api.rtt.io/api/v1/json
    timeout: 10.0
    include_buses: false    # show replacement bus services from RTT
    detail_rows: 8          # services per board that get a calling-points call
```

`SourcesConfig` in `config.py` validates that `primary != fallback` and
that `detail_rows` is between 1 and 12.

## Surface changes

- `/api/status` gains `active_source`, `primary_healthy`,
  `fallback_healthy`, and `credentials: {rdm: bool, rtt: bool}`. Replace
  the single `api_key_present` with that map.
- `/admin` shows the active source in the Status block, edits the new
  `sources` fields, and adds a **Force source** control (`rdm` / `rtt` /
  `auto`, in memory only, not saved to YAML) for testing the fallback
  without pulling the network cable.
- Board frontend: a small source badge in the board footer next to the
  clock, using the existing `stale` styling hooks. Themes may style it but
  need not; hidden by default in `splitflap`.
- Announcements: dedupe keys must survive a source switch, otherwise the
  same train is announced twice under two ids. Change the announcer's
  "already announced" key from `Service.id` to
  `(board.crs, mode, scheduled_time, destination)`. Keep `Service.id` for
  everything else.

## Tests

- `tests/fixtures/rtt_pad_departures.json`, `rtt_rdg_arrivals.json`,
  `rtt_service_detail.json` recorded from the real API with credentials
  stripped.
- `test_rtt.py`: parser parity with LDBWS for the same train (status,
  delay minutes, calling point ordering in both modes), time conversion,
  bus and non-passenger filtering, unconfirmed platform handling.
- `test_sources.py`: failover after N failures, recovery on primary
  success, missing credentials reported once, `Board.source` set.
- Extend `test_announce_scheduler.py` with a source-switch case proving
  no duplicate announcement.
- Config: old `api:` key still loads; `primary == fallback` rejected.

## Deployment

- `deploy/install.sh` prompts for RTT credentials alongside the RDM key
  and writes both to `/etc/describer/describer.env`, mode 600.
- The backend unit reads `%h/describer/.env` **before**
  `/etc/describer/describer.env`, not after. systemd applies environment files
  in order and the last assignment wins, an assignment to the empty string
  included, so a checkout holding a bare `RDM_API_KEY=` silently blanked the
  deployed key. RDM was then a permanent failure from the first tick, the board
  failed over to RTT, and a day's allowance went with it. Deployed credentials
  must be read last. `install.sh` copies the unit; changing it means re-running
  the script (or re-copying) plus `systemctl --user daemon-reload`.
- No new apt or pip dependencies; `httpx` already supports Basic auth.

## Out of scope for this addendum

Merging data from both sources at once, per-station source selection,
a third source, and using RTT service detail to enrich RDM boards.


---

# Addendum 2 — Realtime Trains next generation (supersedes the RTT half of Addendum 1)

Addendum 1 was written against the v1 RTT API. That API is reachable only with
old-portal credentials, which can no longer be created, and it is being turned
off. Everything below replaces the "Realtime Trains API facts" section and the
`RttClient` specifics; the source abstraction, `SourceManager`, failover rules,
config layout and surface changes in Addendum 1 all still stand.

## API facts

- Base URL `https://data.rtt.io`. Register at api-portal.rtt.io (an RTT
  unified login). Specification: realtimetrains.github.io/api-specification.
- **Bearer token**, not Basic auth. The token lives in `RTT_TOKEN`
  (environment or `.env`), never in `config.yaml`, never logged. A token is
  either a long-life *access* token, used as-is, or a long-life *refresh*
  token that buys a short-life access token from `GET /api/get_access_token`
  (which returns `{token, entitlements, validUntil}`). We are not told which
  we hold: try the exchange once, remember the answer, and renew a minute
  before `validUntil`.
- `GET /rtt/location?code=gb-nr:{crs}&timeWindow={minutes}` is the board. It
  returns every service touching the station in the window, each with
  `temporalData` (an `arrival` block, a `departure` block, or both, plus
  `displayAs`, `scheduledCallType`/`realtimeCallType`, `status`),
  `locationMetadata` (`platform.{planned,forecast,actual}`,
  `numberOfVehicles`), `scheduleMetadata` (`uniqueIdentity`,
  `operator.{code,name}`, `modeType`, `inPassengerService`), optional
  `reasons[]` (`type` DELAY or CANCEL, `shortText`, `longText`), and
  `origin[]` / `destination[]`. **There is no separate arrivals endpoint**:
  departures and arrivals are two readings of one response.
- Each temporal block carries ISO datetimes — `scheduleAdvertised`,
  `scheduleInternal`, `realtimeForecast`, `realtimeActual`,
  `realtimeAdvertisedLateness`, `isCancelled` — not v1's `"1432"` strings.
- `GET /rtt/service?uniqueIdentity=gb-nr:W12345:2024-05-14` gives
  `service.locations[]` for calling points, each with the same
  `temporalData` / `location` shapes.
- **Rate limits are real and tight**: a free token allows 10 requests a
  minute, 100 an hour, 1000 a day, reported in `X-RateLimit-Remaining-*`
  headers, with `429` and `Retry-After` when exceeded.

## RttClient specifics

- Departures read `temporalData.departure`, arrivals `temporalData.arrival`;
  a service without the relevant block is not on that board. A
  `ADVERTISED_SET_DOWN` call is not a departure (nobody may board) and a
  `ADVERTISED_PICK_UP` call is not an arrival. `PASS` and `DIVERTED`
  locations never appear.
- Times convert from ISO to `"HH:MM"` at the parser boundary. Lateness comes
  from `realtimeAdvertisedLateness` when the API reports it, otherwise from
  the clock difference, so it matches the LDBWS parser.
- Status mapping is unchanged from Addendum 1: cancelled → `CANCELLED`, no
  realtime → `UNKNOWN`, realtime later than booked → `EXPECTED`, else
  `ON_TIME`.
- `platform` only when `actual` or `forecast` is present; `planned` alone is
  not a confirmed platform, keeping parity with LDBWS.
- `Service.id` is `f"rtt:{uniqueIdentity}"`, which already namespaces and
  dates the train. Announcement dedupe still keys on the train, not the id.
- `length` comes from `locationMetadata.numberOfVehicles`; `cancel_reason`
  and `delay_reason` from `reasons[]`. v1 had none of these.

## Living inside the allowance

- `sources.rtt.min_poll_interval` (default 120 s) is a floor the poller obeys
  whenever RTT is the live source, however low `sources.poll_interval` is.
- `sources.rtt.detail_rows` defaults to 3, not 8: only the first row is ever
  expanded on screen, and each detail row is its own request.
- When the remaining allowance falls below `DETAIL_BUDGET` in any period
  (3 a minute, 25 an hour), the client skips calling-point calls and still
  returns the board. Two stations polling on the floor spend about 60 calls an
  hour on boards alone, so the hourly figure is the one that usually bites.
  Calling points are decoration; a board is not.
- The remaining allowance is surfaced in `/api/status` as `rate_limit` and
  shown in the admin Status block.

## Config

```yaml
sources:
  rtt:
    base_url: https://data.rtt.io
    timeout: 10.0
    include_buses: false
    detail_rows: 3          # rows given a calling-points call
    time_window: 60         # minutes of services per board call
    min_poll_interval: 120  # floor while RTT is live (free tier: 100/hour)
```

## Tests

- `tests/fixtures/rtt_pad_departures.json`, `rtt_rdg_arrivals.json` and
  `rtt_service_detail.json` carry real v2 shapes with values mirroring the
  LDBWS fixtures, so parity assertions compare the same four trains.
- `tests/fixtures/rtt_live_capture.json` is an untouched capture from the
  real API, parsed by a test that exists to catch shape drift.
- `test_rtt.py` additionally covers the token exchange (both token kinds),
  set-down/pick-up filtering, passing points, and dropping calling points
  before dropping the board when the allowance runs low.


---

# Addendum 3 — Board layout, and what a theme module may override

Written after the splitflap board was found truncating its status column on
the real display. The cause turned out to be structural rather than cosmetic,
so this records the sizing model, the measurements behind its constants, and
the extended theme contract. It supersedes the "Themes" description in the
main body where they disagree.

## What was actually wrong

The splitflap status column showed `EXP 15:` and stopped. Each character is a
flap of `min-width: 0.86em` with an `0.08em` gap, so nine of them need
`8.4em`, and the shared row grid gives the status column `6.8em` — room for
seven. `CANCELLED` was being cut the same way; only `ON TIME` fitted, which is
why it went unnoticed.

Widening that one column is not enough. A splitflap row is 39 fixed-width
characters (5 time + 20 destination + 2 platform + 9 status + 3 operator).
At the shared `2.5vh` type size that is about 39 × 0.94em × 27px ≈ 990px of
tiles in a 906px half-screen. **The row never fitted**; the overflow was
simply hidden. A tile grid cannot reflow, condense or ellipsise, so the type
size has to be derived from the width rather than chosen.

## The sizing model

`.rows` is a size container (`container-type: size`), and everything inside is
expressed against it:

```css
--rows: 8;              /* configured rows, set per board from board.js */
--calling-share: 0.9;   /* the stops block, 0 when it is hidden */
--slot: calc(100cqh / (var(--rows) + var(--calling-share)));
--row-font: min(calc(100cqw / var(--char-budget)), calc(var(--slot) * 0.6));
```

- **Height.** Each row takes `flex: 0 0 var(--slot)`, so the *configured*
  number of services fills the container exactly, on a whole screen or on
  half of one. Sizing from the configured count rather than the count in hand
  keeps the type still as trains drop off the board.
- **Width.** `--char-budget` is how many characters of the theme's own font
  one row must afford. The font is whichever of the two constraints binds.

Measured budgets (1920×1080, two boards, so `100cqw` = 906px):

| Theme | Budget | Why | Resulting type |
|-------|--------|-----|----------------|
| modern | 30 | proportional; the fixed columns are 17.6em and the destination needs ~10em | 30px / 62px |
| crt | 33 | monospace at 0.613em per character, so 20 characters cost 12.3em | 27px / 57px |
| 1990s | 33 | the same monospace grid as crt, under a row of column headings | 27px / 56px |
| splitflap | 39 | one tile per character, and tiles cannot be condensed | 23px / 48px |
| nse | 23 | 34 dot-matrix characters at 0.6em, and no operator column | 37px / 53px |
| led-matrix | 23 | the same 34 characters, on a panel with no printed casing | 39px / 59px |
| thameslink | 24 | proportional, and the featured line is a time plus a destination | 37px / 63px |

(Second figure is the single-board layout.)

### The trap that cost the most time

`.stale` is `hidden` — that is `display: none` — for all but a few seconds a
day. With auto-placement that shifted `.rows` out of the `1fr` track into an
`auto` one, and a size-contained element in an auto track resolves to **zero
height**: `100cqh` became 0, the type became 0px, and the board rendered
blank. Every child of `.board` now names its own `grid-row`. Do not remove
those.

## Theme module contract

`board.js` still owns the data and the DOM. A theme module may export any of:

| Export | Called | For |
|--------|--------|-----|
| `attach(boardsEl, options, api)` | on switch | `api.render()` repaints on the theme's own schedule |
| `configure(options)` | on config change | live theme options |
| `detach()` | on switch away | **must** clear timers and caches |
| `renderText(cell, text)` | per cell | replaces `textContent` |
| `statusText(service)` | per status cell | return `null` to accept the default wording |
| `serviceDetail` | read once | `true` shows the position/formation line; absent (or `false`) keeps it hidden, share 0, on every other theme |
| `positionText(service, mode)` | per detail line | return `null` to accept the default wording |
| `renderFormation(el, formation, length)` | per detail line | replaces the default car strip (Addendum 8); no theme uses it now |
| `renderCallingPoints(list, points, rawPoints, service)` | per board | replaces the default paging; `rawPoints` is the service's full `CallingPoint` list and `service` the top service itself (for its `journey`), both ignored by every theme but thameslink |
| `callingPointsLabel(mode, service)` | per board | return `null` to accept "Calling at" / "Called at" |
| `reasonText(service, mode)` | per reason line | return `null` to accept the default wording |
| `renderReason(el, text)` | per board | replaces `textContent` on the reason line |
| `weather` | read once | `true` shows the forecast strip (Addendum 15); absent (or `false`) keeps it hidden, share 0, on every other theme |
| `renderWeather(el, forecast)` | per board | full override of the default strip; `forecast` is the parsed `Forecast` dict or `null`; no theme uses it now |
| `afterRender(boardsEl)` | after a pass | anything left over |

`statusText`, `renderCallingPoints`, `reasonText` and `renderReason` are new.
A theme that wants to repaint on a timer keeps the state itself and calls
`api.render()`; `board.js` then asks it again for the wording, so the timer
and the render never disagree.

## Splitflap specifics

- **Delays alternate.** `statusText` returns `Delayed` and the expected time
  on a 15 s cycle, so nine characters of `Exp 15:23` are never needed. The
  phase is shared by every delayed service on screen, so they flip together.
  `CANCELLED` still needs its nine flaps; the budget above provides them.
- **Names abbreviate before they truncate.** `ABBREVIATIONS` is an ordered
  list applied one rule at a time and only while the name is still too long,
  so `London Charing Cross` becomes `London Charing X` and stops there. A
  trailing `via …` is dropped before any word is cut.
- **Calling points page.** A mechanical board cannot scroll. The stops are
  packed into full-width pages that never split a station name, and turned
  every 6 s. The row width is worked out from the host's own type size and
  the tile pitch (Addendum 14), so it survives a font or size change.

## nse specifics

- Every cell is a `<canvas>`; `nse.js` paints a 5×7 dot font from a column
  bitmap and only repaints a canvas whose discs are turning. A text change is
  swept left to right, `COLUMN_MS` per dot column, paced by the clock so a
  stalled frame catches up. Backing stores follow the CSS box through one
  `ResizeObserver`, so a font or size change redraws crisp.
- The character pitch is `PITCH_EM` (0.1) of the host's font size in the JS and
  `0.6em` per character in the CSS. Change both.
- The operator column is `--chars: 0` and hidden; `renderText` leaves it empty.
- The board is the indicator's casing, and the casing carries only what a
  printer could have put there: the column labels (`TIME`, `TO`/`FROM`,
  `PLAT`, `EXPECTED`) along the top in logo blue, and the logo alone along the
  bottom, in the *last* grid row by CSS. The three-slash logo is a skewed
  gradient on `.board-header::before` with the wordmark on `::after`.
- **Everything that changes is dots.** `board.js` writes the station, mode,
  clock, stops label, messages, the stale warning, the no-services placeholder
  and the reconnecting overlay straight into its own elements with no theme
  hook, so the stylesheet hides all of them and `afterRender` mirrors each into
  a line of dots it owns (`.nse-ident`, `.nse-message`, `.nse-label`,
  `.nse-stale`, `.nse-empty`, `.nse-connection`). Nothing on this sign is
  written rather than flipped: the casing is printed plastic and cannot say
  anything new, so a fault belongs on the matrix like any other message. The
  clock and the reconnecting overlay have no hook at all, so a timer reads
  `.clock` and `#connection` back every `CLOCK_MS`; the overlay especially,
  because `board.js` only toggles its `hidden` flag from the SSE handlers and
  there is no render pass to hang it off.
- The matrix therefore has six claims on its height: `--rows`,
  `--calling-share`, `--heading-share`, `--stale-share`, `--message-share` and
  `--ident-share`. `nse.js` sets `--message-share` and `--stale-share` per
  board, to 0.9 or 0. The fault line takes the top of the matrix, under the
  printed headings; both it and the message line are kept in the tree even when
  empty, because the message line's `margin-top: auto` is what pins it and the
  identification line to the foot of a half-empty board.
- `.connection` is sized `font-size: 0` so only the dots show, which also
  collapses the `em` padding it inherits from `base.css`; both dot themes give
  it a padding in viewport units instead.
- The watchdog must not be re-armed while one is pending. `setTarget` calls
  `start()` for every cell it retargets and the sweep re-enters it on each
  frame, so pushing the deadline back each time meant it never fired — which is
  precisely when frames have stopped and it is needed: the board froze
  part-swept. `armWatchdog` returns early while one is outstanding.
- The stops and the message line turn a page at a time, never splitting a
  name. They cannot scroll: a disc is a fixed place on the board.
- A cell asked for before `nse.css` has arrived (`--chars` computes to the
  empty string) is queued and painted from `afterRender`; `board.js` will not
  ask again for text that has not changed.

## thameslink specifics

- The board is not a list of equals: the top service gets `--feature-share`
  (1.95) slots with its route under it, and the rest are a packed list at
  `--later-share` (0.74) of a slot each under a bar of `--head-share` (0.5).
  What the later trains give up is what the route gets to use. The stops take
  the slack (`flex: 1 1 0`), sized from `--calling-share` so that board.js
  hiding them still hands the height back.
- **The stops are measured against the block, not the track.** The track is a
  `1fr` grid row inside a column flex item, and Chromium sizes that to its
  content rather than to the height flex handed the block, so `clientHeight`
  on the track reads back the whole list. `measure()` works from
  `.calling-points` minus the label's offset instead, then snaps the track to
  a whole number of stops: half a station name under the fold reads as a
  fault, not as a page that continues.
- Paging slides the whole column with a transform rather than swapping the
  text, so the route line runs on across a page turn exactly as it does on
  the real panels, and the page turn stays off the main thread.
- The route takes the leftover height whether it needs it or not
  (`flex: 1 1 0`), so the later trains sit against the foot of the board and
  a train with five stops leaves black between the two. That is wanted, not a
  gap to close: the list holds still from board to board instead of walking up
  and down as the top train changes, and the room is already there when a
  train with a long calling pattern comes along. Do not make that surplus fall
  to the bottom.
- **The trains that are not there still claim their height.** The route is the
  only thing on this board that grows, so a station with four services handed
  it the four missing rows and the blue bar sat a third of the way further
  down than the bar on the other half of a split screen. `.tl-fill` is a
  spacer at the foot of the list of `--missing` × `--later-row`, set by
  `thameslink.js` from the configured `--rows`, so what a short board is short
  of shows as black under its list — where a reader expects it — and the bar
  holds the same line on both halves. It is the *reason the route grows* that
  makes this necessary: the surplus has to be taken away from the route
  before the route can absorb it.
- The stops are re-measured from a `ResizeObserver` on `.calling-points`, not
  only from the page-turn timer. The room for them is settled by flex and is
  still moving while the stylesheet lands and the later trains take their
  share, so the measurement taken during the render is often wrong and only
  the next timer tick corrected it — five seconds of a station name cut in
  half, every time the board loads. Observe the block, never the track: the
  track's height is the one `measure()` sets.
- The next train's block is `flex: 0 0 auto` — exactly its two lines, never a
  share it might not fill. A block sized to a share and centred in it puts
  half its slack between the station name and the train, which reads as the
  board having failed to draw something; sized to a share and aligned to the
  start it merely moves that slack below. `--feature-share` (1.95) stays in
  the slot maths as the *reserve* for the block, and whatever the reserve
  over-provides falls to the route below, which is `flex: 1 1 0` and takes
  it. The block measures 2.04em, which is 1.59 slots where the type is capped
  by the slot, so 1.95 is deliberately more than the block ever needs: the
  reserve is what the *slot* is divided by, and over-providing there sends the
  difference to the route instead of into the type.
- A later train's row is the mean of its own text and the slot it would
  otherwise take: the same line, with half the air. `--later-share` (0.74) is
  then a reserve that no longer matches a row, and that is the point — drop it
  to what a row measures and the slot grows, taking the type and the stops
  with it, so the list tightens and the route gains nothing. Held where it
  was, every pixel a row gives up lands in the route: 4 stops a page against
  3 on a whole screen, and 11 against 8 on half of one.
- The countdown rides the second line, right-aligned against the time and the
  destination and at their size: how long until the train goes is what people
  look up for, so it belongs on the line that says which train, not up among
  the labels. The destination's column is the flexible one, so a long name
  ellipsises before the countdown gives up any room. The first line carries
  only the ordinal and the platform, and the two lines sit on their own
  baselines rather than being centred against each other.
- The status column counts down — "6 min", "Due" — which leaves nowhere to
  print an estimate, so a delayed service alternates the countdown with
  `Exp HH:MM` on the 15 s refresh tick, in one phase shared by the board.
  "Now" comes from the `.clock` board.js keeps against the Pi, not from the
  browser's own clock.
- The clock panel is a mirror. board.js rewrites `.clock` every quarter
  second and compares `textContent`, so a `<sup>` for the seconds inside it
  would be wiped on the next tick; `.clock` is hidden and `.tl-clock` is
  read back on its own timer, as `nse` does.
- `--head-share` is set to 0 from `afterRender` when a board has fewer than
  two services, so the bar claims no height with nothing under it.

## Two dot-matrix themes, one font

`themes/dotmatrix.js` holds the 5x7 glyphs, `columnsFor`, the abbreviation
table, `fit`, `normalise` and `paginate`. It is pure: no DOM, no state. Both
dot themes import it, so the letters can never drift apart, and each still
owns its canvases and decides what one dot looks like.

The two differ in the ways the real machines did, and those differences are
the point of having both:

| | `nse` (flip-dot) | `led-matrix` (LED) |
|---|---|---|
| A change | sweeps column by column, discs drawn edge-on mid-flip | simply appears |
| Too long for the line | **pages**, every 6 s, never splitting a name | **scrolls**, a dot column every 45 ms |
| Frame loop | `requestAnimationFrame` plus a watchdog, for the sweep | none at all; painting is synchronous |
| Dots | reflective discs, cream on black | amber cores with a bloom, over the dark glint of the unlit ones |

A disc is a fixed place on the board and cannot slide sideways, so `nse` must
page; an LED panel is free to scroll. The LED paint is three `Path2D` fills
(dark, bloom, core) costing about 0.7 ms for a full-width scrolling line on a
dev Mac, and there are at most two such lines per board.

Both mirror the same set of text: the two carry the station, mode and clock in
different places (`nse` along the foot of the matrix, `led-matrix` along the
top), but the stale warning, the no-services placeholder, the stops label, the
messages and the reconnecting overlay are lit on both. `led-matrix` hides
`.stale` for the same reason `nse` does — the panel is the whole screen, so
there is no surface left to write a fault on that is not made of LEDs.

`led-matrix` has no config block. Its field would have to be named
`led-matrix`, which is not a Python identifier, and amber is the only colour
those panels came in. The palette is two constants at the top of the module.
`1990s` has none either, for the first of those reasons and because the seven
Teletext colours are not ours to choose. `ThemesConfig` therefore carries five
of the seven themes; a theme whose name is not an identifier cannot have one.

## `hidden` needs saying when the theme sets a display

`base.css` gives `.calling-points` `display: flex`, and an author `display`
beats the `hidden` attribute's UA rule, so `wrap.hidden = true` did nothing:
the block stayed laid out, on `grid-row: 3` of `.board`. Every theme got away
with it because the element then overlapped whatever else was in that row. It
only became visible in `nse` once the fault line let its stale row collapse and
the header moved up, at which point a stray "CALLING AT" appeared under the
logo. `.calling-points[hidden] { display: none; }` is now in `base.css`; any
new rule that sets `display` on an element `board.js` hides needs the same.

## Calling points belong to their service

They now render inside `.rows`, directly under the top service, in every
theme — they describe that train, not the board. `board.js` moves the block
after the first row on each pass and gives it a share of the height, or none
when it is hidden.

The block is one line: the label and the stops together at 0.9 of the row's
type, so the stops read a size below the destination. The marquee is gone.
`board.js` packs the stops into pages that fit the line, never splitting a
name, and turns a page every 5 s; splitflap still does its own paging through
`renderCallingPoints`. Pages are measured by painting candidates into the
list, so the font is part of the cache key: a theme's stylesheet and web font
arrive after the switch, and a page measured in the old face does not fit the
new one. `board.js` therefore re-renders on the stylesheet's `load` and on
`document.fonts` `loadingdone`.

## Why a train is late or cancelled

Both feeds carry the reason as text (`Service.cancel_reason`,
`delay_reason`), worded as a sentence written to follow the status:
"This is due to a shortage of train crew". `board.js` strips that lead-in and
joins the rest on to what the train is doing, giving the wording the
announcements use:

> The 15:24 to Abbey Wood via Whitechapel is delayed due to a fault with the
> signalling system

A reason worded some other way is printed after a colon rather than mangled.

- **The line names its train**, with the time and where it is going — or where
  it is coming from, on an arrivals board. It does not always sit under the
  train it is about, and a board is read from across a platform, so a bare
  "Delayed due to…" under a list of eight trains says nothing useful.
- **It belongs to a train, not to the board.** The line renders inside
  `.rows`, under the top service and under its stops, exactly as the calling
  points do. The board is describing its top service, so that is whose reason
  this is; when that train is running normally and a later one is not, the
  later one takes the line, and the sentence says which train either way.
- Only the reason that matches the state the train is *in* is shown: a
  service running to time may still be carrying the reason it was late an
  hour ago, and printing that is worse than printing nothing.
- `--reason-share` is one more claim on the height, and `board.js` sets it per
  board to 0.9 or to 0. Most of the day it is 0 and the line costs the board
  nothing, so **a theme that writes its own `--slot` must add it** — `1990s`,
  `nse` and `led-matrix` all do. `thameslink` deliberately does **not**: see
  its own section for why.

### It is a sentence, so it never simply fits

Nothing here may be trusted to fit a board line at a size worth reading, and
a reason cut off at the margin is worse than useless — the half that matters
is usually the end of it. Every theme therefore pages or scrolls the whole
text, the way that machine would have:

| Theme | What it does |
|-------|--------------|
| modern, crt, 1990s, thameslink | `board.js` packs it into pages that fit and turns them with the stops, every 5 s |
| splitflap | flaps it, paged with the stops on the drum's own 6 s |
| nse | pages it on the matrix — a disc is a fixed place on the board |
| led-matrix | scrolls it, a dot column at a time — an LED panel can |

The default pager measures by painting candidates into `.service-reason-text`,
a box inside the line that grows and shrinks against everything else on it, so
what it reports is the room a theme's own prompt or label has left it, in that
theme's font and capitals. Never split a word across a page.

Colour is each theme's own: `modern` takes the colour of the status it
explains, `crt` a terminal prompt and the cancelled blink, `1990s` Teletext
green and red, `thameslink` amber and red. The dot themes have one colour of
dot, so what marks the line out there is where it is.

## Verifying this by hand

- The animation needs `requestAnimationFrame`, which a hidden browser pane
  does not run: the flaps freeze mid-alphabet (the watchdog still steps them,
  four times a second). A headless Chrome driven over CDP does deliver frames
  at 60 Hz and is how Addendum 14 was measured.
- `setInterval` is throttled in a background tab, so the 15 s status cycle and
  the 6 s page turn only run while the pane is displayed.
- Theme CSS was cached hard, which cost a deployment: the Pi drew a new theme
  module against the previous release's stylesheet. `/static` and both pages now
  send `Cache-Control: no-cache`, so the browser revalidates every file and the
  ETag turns that into a 304. A browser that cached a file *before* that change
  still holds it, so clearing `~/.cache/chromium` once is what unsticks a Pi;
  in a pane, re-fetch with `{cache: 'reload'}` or append a query string.
- Truncation is easier measured than seen:
  `[...document.querySelectorAll(".cell")].filter(c => c.scrollWidth - c.clientWidth > 1)`.
  That test reads a splitflap cell wrong: its flaps live on one canvas, so
  the cell never overflows. Compare the canvas's `getBoundingClientRect()`
  right edge with its cell's instead, and check `canvas.__chars` against the
  `--chars` for its field — a mismatch is the bug below, not a long name.
- A board with fixture data proves very little. The recorded fixtures hold
  short destinations; the names that break a layout are `London Charing Cross`,
  `Ashford International` and `Abbey Wood via Whitechapel`. Test with those.
- An RTT board with no calling points usually means the rate-limit guard
  dropped the detail calls (see Addendum 2), not a rendering fault.

## A row must be in the tree before its cells are painted

`renderRows` used to build a row from the template, paint all five cells, and
`append` it afterwards. A detached element has no computed style, so
`getComputedStyle(cell).getPropertyValue('--chars')` came back empty and
`renderText` fell back to the length of the text it was handed. Every splitflap
cell therefore got a grid exactly as wide as its own text: `OXFORD` was six
flaps, `ON TIME` was seven, and `ABBEY WOOD VIA WHITECHAPEL` was twenty-six,
which is where the overflow came from. Nothing was ever padded, and the
abbreviation table never fired, because a name is never longer than itself.

`append` now happens first; it still doubles as the ordering step, since
appending a row that is already in `.rows` moves it. `setText` short-circuits
on `dataset.rendered`, so a cell painted wrong once stays wrong — anything
reading layout out of CSS has to run against a connected node. The canvas
that replaced the flap children (Addendum 14) is sized from the same computed
style, so the rule stands.

## Open items

- Verified at 1280×720 and 1920×1080, one and two boards, all seven themes:
  rows fill 100% of the height, no page overflow in either axis, and no cell
  clips except in `modern` and `crt` (see below). The model is width-bound at
  both sizes and the two are proportional, so 720p is not a separate case —
  what fits at 1080p fits at 720p, smaller. `thameslink` is the exception to
  the width-bound half of that: its type is capped at `0.78` of a slot, which
  binds on a whole screen and not on half of one, so both counts were checked
  rather than inferred. It fits `Abbey Wood via Whitechapel` at either.
- `modern` and `crt` still ellipsise a destination past about 10.4em
  (`Abbey Wood via Whitechapel`, `London Charing Cross` on a split screen).
  That is the proportional themes working as designed, but they have no
  equivalent of splitflap's `ABBREVIATIONS`; giving them one would buy back
  several characters.
- Splitflap at 720×2 boards renders at 15.5px. It measures correctly and it is
  what the 39-character budget allows in half of 1280, but it wants judging on
  the real screen, not in a browser pane.
- `0.6` in `--row-font` and `0.9` for `--calling-share` are judged by eye, not
  derived. `--calling-share` was 1.3, which reserved 89px for 53px of label and
  stops; 0.9 is the measured content plus air, and it is the safe ceiling only
  because the block's type is capped at half the row's. They are still the
  first things to adjust if the board looks wrong.
- `--calling-share` has a second home in `board.js`, which sets it per board to
  the same number or to 0. Change both.
---

# Addendum 4 — Never spend the RTT allowance on local work

The free RTT token allows **10 calls a minute, 100 an hour, 1000 a day**, and
it is one allowance shared by every machine holding the token. The Pi polling
two stations on the 120 s floor already spends about 60 board calls an hour,
so a dev session that reaches the live API does not just waste quota — it
takes the real board off RTT for the rest of the hour and burns the daily
budget by mid-afternoon. RDM has no comparable limit; RTT is the scarce one.

## The rule

**Local runs and tests never call `data.rtt.io`.** Every RTT behaviour is
already exercisable from `tests/fixtures/rtt_*.json`. Live RTT calls are for
the Pi, and for a deliberate capture the user has asked for.

In practice, on the dev Mac:

- Leave `RTT_TOKEN` unset in the dev shell and out of the repo `.env`. A
  source with no credentials is a permanent failure reported once at startup
  and never retried (Addendum 1), so this alone is enough to make live RTT
  calls impossible. It is the primary guard; the rest are belt and braces.
- Set `sources.fallback: null` in the local `config.yaml`, so a failing RDM
  key cannot quietly turn into RTT traffic.
- Never use the admin **Force source** control, `/api/source/force`, or
  `sources.primary: rtt` on a dev machine. Forcing bypasses the failover logic
  and puts every poll on RTT immediately.
- Never `curl`, `httpx` or otherwise poke `data.rtt.io` "just to see the
  shape". The shape is in `tests/fixtures/rtt_live_capture.json`, which exists
  for exactly that, and `test_rtt.py` parses it to catch drift.
- Never leave a dev server polling unattended. Even on RDM it is pointless;
  stop uvicorn when the check is done.

## Tests make no network calls at all

`pytest` must be runnable with the network down. Both clients keep a pure
`parse_board(...)` that works on recorded JSON precisely so this holds. A test
that needs an HTTP layer mocks the transport; it does not reach an upstream,
and it does not read `RTT_TOKEN` from the developer's environment. The autouse
`clean_credentials` fixture in `conftest.py` deletes `RDM_API_KEY` and
`RTT_TOKEN` for every test; the ones that need a key opt in to the
`rdm_credentials` / `rtt_credentials` fixtures, which set obvious fakes. Do not
undo that, and add any new credential variable to it.

If a test would need a live call to be meaningful, it does not belong in
`pytest`; record a fixture instead.

## Recording a new fixture

The one legitimate reason to spend live RTT calls. It is a deliberate,
user-approved act, not a step inside another task:

- Ask first, and say how many calls it will cost.
- One capture, one station, one window. Save the raw response verbatim to
  `tests/fixtures/`, strip the token from any header dump, and commit it.
- Do not loop, do not poll, do not re-run to "get a fresher one".

## If the allowance has already been spent

`/api/status` reports `rate_limit` from the `X-RateLimit-Remaining-*` headers,
and the admin Status block shows it. A `429` carries `Retry-After`. Nothing
resets it early — wait out the window. Do not register a second token to work
around a limit hit by local testing; fix the testing instead.

---

# Addendum 5 — The palette a theme hands over

`modern` and `thameslink` take their colours from config. The rest do not, and
the reason is that their colours are not a palette: `crt` has a phosphor,
`splitflap` has flaps, `1990s` has the seven Teletext colours by name, and the
two dot themes have one colour of dot. Renaming those into a semantic
vocabulary would say something untrue about them.

## The vocabulary

Seven roles, named the same in every stylesheet that has them, which is what
lets one config model and one admin panel serve any theme:

| Role | Property |
|------|----------|
| `background` | `--bg` |
| `text` | `--fg` |
| `dim_text` | `--muted` |
| `accent` | `--accent` |
| `on_time` | `--on-time` |
| `late` | `--late` |
| `cancelled` | `--cancelled` |

`modern` already spoke this; `thameslink`'s `--tl-*` were renamed into it. A
theme uses the roles it has: `thameslink` counts down in `--fg` and therefore
has no `on_time`, and its `--tl-bar` and `--tl-rule` stay in the stylesheet
because they are structure, not palette (**partly revised by Addendum 11**,
which gives `--tl-bar` a control after all). **A theme declares its own
roles** in its module and passes that list to `applyColours`, so a role it
does not have is never written.

## How a colour reaches the board

`themes/colours.js` is the only thing that writes one. It is not a theme: no
DOM, no state beyond the properties it has set. A theme calls `applyColours`
from `configure` and `clearColours` from `detach`; the properties land on
`document.documentElement`, where they beat the stylesheet's own `:root` and
come off again cleanly on a theme switch. Nothing in the backend knows a
colour from a flap speed — it is an ordinary theme option on the path config →
`/api/config` → `poller.state()` → SSE → `board.js`.

An unset colour is `null`, not today's hex. **The stylesheet stays the source
of truth**, so a theme that is redrawn later still reaches a board whose owner
once opened the picker.

## Tints are derived, never written twice

`--rule` and the selected-row wash in `modern`, and `--tl-rule` in
`thameslink`, are `color-mix()` of the tokens they are made from. A recoloured
board keeps its hairlines in step with its text and its selection tint in step
with its accent. The Pi runs Chromium 152, so `color-mix` is safe; anything
added in this vein should be mixed rather than spelled out.

## /admin

- The swatch defaults are **read from the theme's stylesheet** at load, not
  copied into `admin.js`. There is no second home for the palette.
- Every colour is measured against the ground, the status colours included,
  and a ratio under 4.5:1 is marked. It still saves: it is your board.
- A mock board row is drawn in the chosen colours, because the alternative is
  a walk to the monitor.
- `readForm` turns a colour field marked `data-unset="1"` into `null`. A new
  field type that has no empty state needs the same treatment.

---

# Addendum 6 — Profiles: a different board at a different time of day

The board is read for different reasons at different hours. At half past
seven it answers "can I still make the 07:42", in the middle of the day
nobody is looking at it, and at six in the evening it is the other direction
from the other station. One `stations:` block and one theme cannot serve all
three, and editing `/admin` twice a day is not a feature.

A **profile** is a named window of the week carrying a sparse override of the
config. The one in force is resolved on every poller tick; everything
downstream reads the resolved config instead of the file's. **A config with no
profiles resolves to itself**, so this changes nothing for a board that does
not use it.

## Shape

```yaml
profiles:
  enabled: true
  entries:
    - name: Morning rush
      days: [mon, tue, wed, thu, fri]
      start: "06:30"          # start == end is all day; start after end wraps midnight
      end: "09:30"
      stations:                     # present = replaces the list wholesale
        - {crs: ABW, mode: departures, rows: 10, platforms: ["1"]}
        - {crs: LBG, mode: arrivals,  rows: 10}
      display: {theme: thameslink}  # a key left out keeps the base value
      announcements: {enabled: true, lead_time: 180}
    - name: Evening
      days: [mon, tue, wed, thu, fri]
      start: "16:30"
      end: "19:30"
      stations: [{crs: LBG, mode: departures, rows: 8}]
      display: {theme: splitflap}
```

- **Ordered, and the first match wins.** Overlaps and gaps are legal rather
  than rejected, because the alternative is a validation error standing
  between the user and a board. What falls through the list is the base
  `stations:` / `display:` / `announcements:` already in the file, which
  makes the config you have today the "every other hour" template without
  anyone having to write one.
- `start`/`end` wrap midnight exactly as `schedule:` does, and `days` names the
  weekdays it applies on. They are not `from`/`to` because `from` is a Python
  keyword, and the alias needed to keep the YAML key would have had to be
  threaded through every `model_dump` in the app to come back out again.

## What a profile may override, and what it may not

| Overridable | Not |
|---|---|
| `stations` (whole list) | `sources` |
| `display.theme`, `clock`, `show_calling_points` | `display.resolution` |
| `display.themes.<theme>` — options and the Addendum 5 palette | `schedule` |
| all of `announcements` bar the Piper paths | |

- **`stations` is replaced, never merged.** Merging two lists positionally
  would have to decide what a half-specified second station means, and the
  thing a profile wants is a *different* station, not a tweaked one.
- **`sources` is out.** Poll intervals and failover are plumbing, not
  presentation, and a profile that could set `poll_interval` or `primary` is a
  way to spend the RTT allowance on a schedule without noticing. Addendum 4
  stands.
- **`display.resolution` is out.** It is a hardware action with a retry loop
  behind it (`_apply_display_mode`), and no template needs the monitor to
  change mode at half past six.
- **Piper's paths are not overridable.** `voices_dir`, `piper_binary` and
  `cache_dir` describe the machine, not the hour.
- The override models are their own all-optional pydantic models. They cannot
  reuse `DisplayConfig` or `AnnouncementsConfig`: those carry defaults, and a
  default is indistinguishable from an override once it is parsed, so every
  unset field would silently overwrite the base.
- `null` inside an override means "not set here" and is dropped before the
  merge, so a profile cannot push a key *back* to null. The one place that
  bites is a palette role: a profile cannot return a colour the base config
  sets to the stylesheet's own. Nothing needs it, and the admin page does not
  offer per-profile colours at all.
- **A theme's options are typed loosely** (`themes: dict`), because a theme's
  options are its own. What proves them is the merge: `Config` validates every
  profile against itself at load and at Save, so a bad `phosphor` is refused at
  the button rather than at 06:30. That error arrives as a `ValueError` inside
  pydantic's `ctx`, which does not survive JSON — `/api/config` reports errors
  with `include_context=False`.

## Profiles choose content; `schedule:` still owns the power

Two time systems in one file is awkward, and the temptation is to fold the
display schedule in as an "off" profile. Don't. `schedule:` blanks the HDMI
output and stops the poller; a profile only decides what is on a screen that
is already awake. **The schedule wins**: while the display is off no profile
is active and nothing is fetched.

## `describer/profiles.py`

Pure — no I/O, no state:

- `in_window(on, off, now)`, factored out of `schedule.py`, which already
  gets the midnight wrap right. `window_for` and `is_display_on` are rewritten
  on top of it so there is one implementation of that arithmetic, not two.
- `active_profile(config, now) -> ProfileConfig | None`
- `resolve(config, now) -> Config` — deep-merges the override and returns a
  fully validated `Config`. Cached on the profile name and the base config's
  identity; it is asked for on every tick and the answer changes four times a
  day.

## `ConfigStore` grows a second accessor

This is the whole of the backend change, and getting it wrong is what will
hurt:

- `get()` — **raw**, exactly as today. `/api/config` GET and PUT and the admin
  page must see the file, not the resolution of it, or saving at 07:00 writes
  the morning profile into the base config.
- `active(now=None)` — resolved. The poller, `state()`, the announcer and
  `/api/status` all read this.
- `force_profile(name | None)` — memory only, never written to YAML, mirroring
  `force_source`. Seeing the evening board at eleven in the morning is the
  only way to check it without waiting.

Every `self._store.get()` in `poller.py` becomes `active()`. `_tick` must take
**one** snapshot and hand that same object to `_notify_listeners`: the
announcer indexes `config.stations[index]` against the boards it was given, so
a resolution taken twice in one tick can pair a board with another profile's
station.

## Switching profiles, in the poller

Detected in `_tick`, beside the existing `is_display_on` check. On a change:

- log at INFO, and `_publish(self.state())` straight away, so the browser
  re-themes and re-lays-out without waiting for the next poll;
- clear `_next_due` and `_failures` — a station that has just appeared has no
  board and must be fetched now;
- **prune `_boards` of every slot not in the new station set.** `boards()`
  hands back a cached board without looking at its age; only a *failed* fetch
  ever marks one stale. Left alone, an evening profile returning to a morning
  station would render a three-hour-old board as live, with no warning on it
  at all. Slot keys already carry the CRS and mode, so nothing collides — the
  stale cache is the only hazard.

The forced re-poll costs one board call per station per switch. Four switches
a day is eight calls, immaterial against RTT's hourly allowance, and the
`min_poll_interval` floor applies as normal afterwards. Do not special-case it.

## The announcer forgets on age, not on absence

`_prune` used to keep only the identities present on the current boards, so a
station leaving the board dropped everything announced for it, and restoring
that station later in the day announced every train on it again. `_announced`
now carries a last-seen timestamp and is pruned against `FORGET_AFTER` (thirty
minutes) instead. Profiles are what would have made that happen daily rather
than never, which is why it was fixed alongside them.

## Surfaces

- `/api/status` gains `active_profile`, `forced_profile`, `next_profile` and
  `profile_changes_at`; the admin Status block shows them, and a **Force
  profile** select sits next to Force source.
- `/api/state` carries `active_profile` so a theme could name it. None do.
- **The board needs no change.** `render()` re-applies the theme from every
  state frame and `ensureBoards(state.boards.length)` already copes with two
  boards becoming one; `detach()` clears the outgoing theme's timers. What
  wants checking by hand is a live switch *into* `nse` or `led-matrix`, where
  the canvases size themselves from a `--chars` that arrives with the
  stylesheet a moment after the switch (Addendum 3).

## /admin, and the trap in it

- `readForm` sweeps every `[name]` field and `set()`s it by dot path.
  **Profile fields must stay out of that sweep**, or a field named
  `display.theme` inside a profile card writes the base theme. Follow the
  pattern `stations` already uses: read the editor with its own
  `readProfiles()` and assign `draft.profiles` after the sweep.
- `renderStations` / `readStations` are wired to `#stations`. Generalise both
  to take a container element and reuse them in the profile editor rather than
  writing a second station card.
- **The theme options and the colour picker are singletons keyed by element
  id** — `#theme-options`, `loadDefaults`, `syncColours`, `renderPreview` all
  assume one theme is being edited. Do not render one per profile. The
  Profiles tab is a *list* plus **one editor for the selected profile**, which
  reuses that machinery unchanged against the selected profile's draft, is
  less code, and is a better editor than eight cards competing for the width.
- A 24-hour coverage ribbon per weekday earns its hour: first-match-wins is
  only hard to reason about until you can see the gaps and the overlaps. It
  samples every ten minutes and **repeats the window arithmetic in JavaScript**,
  because the answer has to be drawn from the unsaved draft. `coversMinute` and
  `in_window` say the same thing twice; change both.
- The editor writes a profile's theme *options* but not its colours. A palette
  belongs to a theme rather than to an hour, so every period showing thameslink
  shows the same thameslink; the model still carries a per-profile palette
  written by hand.
- `THEME_OPTIONS` in `admin.js` describes the same options the Display tab
  spells out in HTML. A new theme option belongs in both.
- The `novalidate` rule holds. Anything `required` added to this panel must be
  reachable by `revealTabFor`.

## Tests

- `test_profiles.py`: window matching including a wrap past midnight, weekday
  filtering, first-match precedence, no match resolving to the base config,
  a sparse merge keeping base values, `stations` replaced rather than merged.
- `test_poller.py`: a switch prunes the boards it must not reuse, re-polls,
  and publishes state.
- `test_announce_scheduler.py`: a station removed and restored does not
  announce its trains twice.
- `test_config.py`: a file with no `profiles:` key loads unchanged; duplicate
  names and malformed times are rejected.
- `test_app.py`: `/api/config` returns the raw config while `/api/state`
  reflects the active profile.

## Out of scope

Per-profile `sources`, dates and one-off overrides (a bank holiday, an
engineering weekend), profiles triggered by anything but the clock, and
transitions of any kind — a profile change is a cut, not a fade.

---

# Addendum 7 — Where the train is now, and how it is made up

Adds one line under the top service, in `modern` and `thameslink` only: where
it is, worked out from the stops it has already left, and its formation, one
box per coach filled to how busy the feed says it is. Every other theme is
untouched — the line's flex share is nought unless the active theme opts in,
and a theme that has not never gets asked for either.

## What the step 0 capture found, against the plan's five assumptions

One `GetArrDepBoardWithDetails` call at LBG
(`tests/fixtures/lbg_arrdep_formation.json`) confirmed three of the plan's
five assumptions and corrected two:

1. Held — a departure carries `previousCallingPoints` alongside
   `subsequentCallingPoints`.
2. Held — a previous calling point's `at` is the actual departure there, and
   is `"On time"` as well as `"HH:MM"`; `"No report"` also appears, meaning
   the train has not reached that stop yet.
3. **Half held.** `coaches[]` does carry `coachClass`,
   `loading`/`loadingSpecified`, `number` and `toilet` — but `toilet` is a
   nested object (`{status, Value}`, `Value` one of `"None"` /
   `"Standard"` / `"Accessible"`), not a flat value, and **`formation.avgLoading`
   does not exist anywhere in the response.** `Formation.average_loading` is
   computed instead: the mean of the coaches with `loadingSpecified: true`,
   rounded to the nearest whole number, `None` when none of them are.
4. **Unconfirmed, not failed.** No service in the capture was at-platform, so
   `eta`/`etd`/`at` never read anything but a time, `"On time"` or
   `"No report"`. There is no "At platform" wording; `positionText`'s
   `approaching` case ("next stop here") already covers that case honestly.
5. **Corrected.** The plan assumed coaches arrive front-first. The capture
   showed the service itself carries `isReverseFormation` (bool), which the
   parser reads instead of guessing: `Coach` order reverses when it is set.
   Every service in the capture had it `false`, so `true` is unverified
   against real data, but the logic no longer depends on an assumption about
   which end is the front.

The capture also surfaced two things the plan had not anticipated: every
calling point, previous and subsequent alike, carries an explicit `isCancelled`
boolean, more reliable than the `et`-text heuristic the existing parser used —
`_calling_points`'s cancellation check now ORs the two, so it reads correctly
on real data without disturbing the old hand-written fixtures that only ever
set `et: "Cancelled"`. And a calling point can carry its own `formation`
(a forecast for that point in the journey); unused here, since only the
service's own formation feeds `Formation`.

## Data model (`rail/models.py`)

`Position` (`state`: `"not_started"` / `"between"` / `"approaching"`, `last`,
`last_time`, `next`, `stops_away`), `Coach` (`number`, `first_class`,
`accessible_toilet`, `loading`), `Formation` (`coaches`, `average_loading`).
`Service.position` and `Service.formation`, both `None` by default.
`CallingPoint.actual_time`, `None` until the stop is reported left.

## LDBWS parser (`rail/ldbws.py`)

- `_actual_time(point)` normalises `at` the way any Darwin estimate is:
  `"On time"` becomes the point's own `st`, `"Delayed"` / `"No report"` /
  empty become `None`, anything else passes through. Shared by
  `_calling_points`, which now sets `actual_time` on every point, and by
  `_position`.
- `_position(raw)` returns `None` when the service is cancelled or has no
  `previousCallingPoints` at all (it starts here). Otherwise it walks the
  non-cancelled previous calling points and finds the last one with a known
  `actual_time`: none found is `not_started` (`next` is the origin); all
  found is `approaching`; some found is `between`, with `stops_away` counting
  what is left. It reads `previousCallingPoints` straight off the raw service
  dict regardless of the board's mode, exactly as the plan specified — a
  departure gets a second read of the same list an arrival already parses.
- `_formation(raw)` returns `None` with no `formation` or no coaches.
  `first_class` is `"first" in coachClass.lower()` — only `"Standard"` was
  ever observed, so this is unverified against a real first-class coach.
  `accessible_toilet` is `toilet.Value == "Accessible"`. Coaches reverse when
  `isReverseFormation` is set. `Service.length` is backfilled from
  `len(coaches)` when the feed's own `length` is falsy.

RTT sets neither field; `test_rtt.py` asserts `position is None` and
`formation is None` on every RTT fixture, and that `length` is unaffected.
Revisiting this — a fresh read of RTT's per-stop `realtimeActual` for the top
row only, if the allowance has room — is future work, not done here.

## Config

`display.show_position` and `display.show_formation`, both default `true`, in
`DisplayConfig` and `DisplayOverride` beside `show_calling_points` — same
mechanism, same profile-override behaviour, each field independent of the
other. No new theme options and no new palette roles: the loading colours are
mixed from roles the themes already have.

## Frontend

### DOM and sizing

`.service-detail` (holding `.service-position` and `.service-formation`)
lives in the board template beside `.calling-points` and `.service-reason`.
`--detail-share` joins `--calling-share` / `--reason-share` in `base.css`'s
default `--slot`, at `0` unless the active theme opts in and there is
something to show. This is inert for every theme that defines its own
`--slot` without the term (`1990s`, `nse`, `led-matrix`, `thameslink`) and a
literal `+0` for the two that use the shared default (`crt`, `splitflap`), so
none of the five changed at all — verified by measuring the computed
`--slot` value and row height for each, not by eye.

### `board.js`

- `placeAfter(rowsEl, el, ...candidates)` replaced the two bespoke
  "anchor after the last visible thing" blocks that used to live in
  `renderCallingPoints` and `renderReason`; both now call it, and
  `renderDetail` does too. The order under the top service is
  row → detail → stops → reason.
- `renderDetail(boardEl, services, board, display)` checks
  `theme?.serviceDetail` first: every other theme gets `wrap.hidden = true`
  and `--detail-share: 0` unconditionally, before position or formation is
  even computed. When the theme opts in, the line hides when the board is
  stale, when both toggles are off, or when there is nothing either would
  draw. `show_position` / `show_formation` missing from a frame (true before
  Addendum 7's config fields reach `/api/state`, which they now always do)
  reads as on, so the toggles are additive rather than a prerequisite for the
  feature working at all.
- `positionText(service, mode)` is a theme hook of the same name, `null`
  accepting the default wording: `not_started` → "Not yet left X";
  `between` → "Left X 14:21 · 3 stops away" ("1 stop away"); `approaching` →
  "Left X 14:21 · next stop here".
- `renderFormation(el, formation, length)` is a full-override theme hook,
  like `renderCallingPoints`. The default builds one `.coach` span per coach
  (or, with no formation, `length` plain ones), each carrying `data-load`
  (`quiet` / `moderate` / `busy` / `unknown`, from `QUIET_BELOW = 35` and
  `BUSY_FROM = 70`), `--load` (0–1, for the fill), `data-first` and
  `data-toilet="accessible"` where they apply.
- `renderCallingPoints`'s theme hook gained a third argument, the service's
  full `calling_points` list; every theme but `thameslink` ignores it. The
  module contract table above now carries all of this.

### `modern`

The 0.9 slot share decided up front, rather than folding the line into the
calling-points pages. Coaches are boxes outlined in `--muted`, filled from
`--on-time` / `--late` / `--cancelled` mixed with `--bg`; unknown is an empty
outline. First class gets a small "1" floated above the box rather than
overlaid on it, so the fill never has to compete with it for the same pixels
— stacking a mark inside the box would have needed a real child element with
its own `z-index`, since a pseudo-element painted after `::after` still sits
under it in paint order.

### `thameslink`

The line sits between the featured train and its route, `flex: 0 0 auto` and
deliberately outside `--slot` — the same reasoning as `--reason-share`: the
route holds the slack and simply turns one more page. Coaches fill `--fg`,
busy ones `--late` (this board has no green); a thin `color-mix()` outline
was added so an all-unknown formation still reads as coaches rather than
empty space, which the plan did not specify — judged by eye, in the manner of
Addendum 3's other unspecified constants.

**Arrivals route marker.** `renderCallingPoints(list, points, rawPoints)`
reads `rawPoints[i].actual_time` to find the first stop the train has not yet
left. Everything before that index gets `.tl-passed`, which dims the name and
the line together in one `opacity` rule, since both are painted by the one
element and its colour-inheriting pseudo-elements. `.tl-final` — the filled
dot — moves to that first-unreached stop instead of the last one, or stays on
the last stop once every one has been left ("arriving next"). A departures
route is unaffected: `rawPoints` is only consulted in arrivals mode.

## Decisions settled during the build

- The strip draws the front on the left, for both themes; no per-station
  `formation_front` setting.
- `QUIET_BELOW = 35` and `BUSY_FROM = 70` stand as named constants in
  `board.js`, unrevised — the LBG capture's loading figures are a 0–100
  scale, as assumed, giving no reason to think Darwin uses anything else.
- `Formation.average_loading` is computed by the parser rather than dropped,
  since `formation.avgLoading` turned out not to exist; `isReverseFormation`
  is applied rather than ignored. Both are corrections to the plan, not
  choices it offered.

## Tests

`test_ldbws.py` (every position state including cancellation-skipping and a
hand-built `not_started` case, formation parsing, `isReverseFormation`,
length backfill — against the real LBG fixture plus small hand-edited cases,
as the plan asked), `test_rtt.py` (both fields `None` on every RTT fixture),
`test_models.py` (defaults and serialisation), `test_config.py` and
`test_profiles.py` (the toggles default on, a profile can silence either
independently), and `test_app.py` (`/api/state` carries both fields through
the real LBG fixture). No frontend test harness; verified by hand in the
browser against Addendum 3's checklist — 1080p and 720p, one and two boards,
`Ashford International` and `Abbey Wood via Whitechapel`, a 12-coach
formation, a formation with no loading figures, a stale board — and by
measuring, not eyeballing, that `crt`, `1990s`, `nse`, `splitflap` and
`led-matrix` render exactly as they did before.

## Out of scope

Position on RTT boards (the detail call could carry it, but only fresh, and
a fresh read costs a call per board poll that the allowance cannot spare);
the places a train passes through; positions for anything but the top
service; announcing loading ("the front four coaches are quieter"); formation
changes along the route; the other five themes; a real first-class or
reversed-formation service to check `first_class` and the reversal against.

---

# Addendum 8 — The whole journey, and a formation you can read

Three changes to `thameslink`, and one to failover that the Pi forced.

## The position line was off the board's edge

`.service-detail` and `.service-reason` carried `padding: 0 0.5em`, so at
720p split screen the "Left X" text started 7.7px right of the time, the
ordinal and "Calling at", and the formation stopped 7.7px short of the
countdown. Both are flush now: everything under the top train starts on one
edge and the formation ends level with the status column. Measure it —
`getBoundingClientRect().left` of `.cell.time`, `.service-position`,
`.calling-points-label` and `.service-reason-text` must agree.

## Formation

The strip was twelve 8.5×15.5px boxes squeezed beside the position text, and
it could never show a facility: `.coach` in `base.css` had `overflow: hidden`,
which clipped the first-class "1" drawn above the box, and nothing anywhere
styled `data-toilet`. The overflow is gone (the fill inherits the radius
instead), which un-hides the flag in `modern` too.

`thameslink` now paints its own through `renderFormation`: a line of its own
under the position text, one car per coach sharing the width up to 2.6em,
rounded at both ends, and a wider gap where the unit letter in the coach
number changes (`A6` → `B1`). The coaches arrive front-first (the parser
reverses them on `isReverseFormation`), so the front is the left end, and a
small arrowhead before the first car says so — which is only as true as
that flag, still unverified against a reversed train. Inside each car, over
the fill: "1" for first class, and the wheelchair sign with "WC" for an
accessible toilet, faded and struck through when the feed says
`NotInService`. A mark sits on a full car as often as an empty one, so each
has a rim of the ground colour: the text by `-webkit-text-stroke` painted
under its fill (`paint-order: stroke fill`), the sign by a second mask of
the same drawing stroked wider, laid beneath it. The sign is an inline SVG
used as a mask over a colour — Pi OS Lite has no emoji font. The marks
were first drawn on a line under the cars; they moved inside at the user's
request.

**`modern` draws the same formation.** The drawing lives in `board.js`'s
default `renderFormation` and in `base.css` (`.cars`, `.car-front`, `.car`,
`.car-marks`, `.car-first`, `.car-wc*`, `.car-count`), whose colours are
`thameslink`'s; `modern.css` overrides only the colours, filling a car green,
amber or red by load band as its old boxes did. No theme overrides
`renderFormation` any more, and the loading bands have one home again
(`QUIET_BELOW` / `BUSY_FROM` in `board.js`). In `modern` the formation takes
a line of its own under the position text, because cars small enough to
share that line cannot hold their marks: `.service-detail` is a column in
`base.css`, and `board.js` sets `--detail-share` to 0.9 per line shown — 1.8
with both, 0.9 with one, 0 with neither. That share is only felt by `modern`,
whose `--slot` counts it; `thameslink` keeps the block out of its slot. A board with only a length
(RTT) reads "10 coaches" rather than ten empty outlines. The loading bands
are duplicated from `board.js`; change both.

**Only accessible toilets are marked, by decision.** Standard toilets are
noise at this size, and the feed has nothing about wheelchair spaces;
inferring them from the accessible toilet's coach would be a guess that
could send someone to the wrong door.

`Coach.toilet_in_service` is new, false only on `NotInService`. "Mixed" now
counts as first class. Step 0 captured LBG, BFR, PAD and KGX: BFR and KGX
carry no formation at all, and PAD's GWR trains call every coach
"Standard" and sometimes omit `toilet` entirely — so first class is still
unseen in real data and still unverified.

## Show full journey

`display.themes.thameslink.full_journey` (default off, in `/admin` and
`THEME_OPTIONS`, overridable per profile). When on, the route is the top
train's whole run in place of "Calling at", labelled "Journey":

- `Service.journey` is built by both parsers: previous stops, this station
  (`CallingPoint.here`), subsequent stops. LDBWS already sends both halves on
  the combined board, and RTT's detail call already returns every location,
  so it costs no request. Where a train divides, the first group on each side
  is taken; the others are another train's route.
- `Poller.boards()` empties `journey` on every service but the top one,
  after the filters, so the one that keeps it is the one on screen.
- Stops left behind are dimmed, this station is a white ring, the
  destination keeps the filled dot, and a white arrowhead sits at the top of
  the next stop the train will reach (only stops before this station carry an
  actual time). On RTT nothing carries an actual time, so no arrow.
- It opens on the page the train is on and pages forward from there,
  recomputed on every paint until the first turn because the room is still
  settling while the board loads.
- A service with no journey (an RTT row whose detail call the allowance
  guard skipped) falls back to "Calling at".

The route's key now includes each stop's classes, so a stop being left
repaints; before, an arrival's dimming only moved when the names changed.

## Failover counts consecutive failures, as Addendum 1 always said

On 2026-09-12 RDM answered NBC and returned 500 "The service is currently
unavailable" for SYD. `SourceManager` never reset `_failures` on a primary
success, so SYD's failures between NBC's successes reached `failover_after`
and took both boards to RTT, losing position and formation and spending the
allowance; each recovery probe that landed on NBC switched back only for SYD
to trip it again. A primary success now resets the count: one station the
primary cannot serve goes stale on its own, and the other stays on RDM.

---

# Addendum 9 — Scrolling the route instead of paging it

`display.themes.thameslink.scroll_route` (default off, in `/admin`, in
`THEME_OPTIONS`, overridable per profile) makes the route glide rather than
turn a page. It applies to "Calling at" and to the full journey alike.

- The column glides down to its last stop at `scroll_speed`, rests
  `SCROLL_HOLD_MS` (3 s), glides back to the top at `return_speed`, rests, and
  goes again. The speeds are in **stops a second** (defaults 0.5 and 4), not
  pixels, so they mean the same at 720p and 1080p and on either half of a
  split screen. Down is `linear`, because it is being read; the return is
  `ease-in-out`, because it is not.
- Each leg is one CSS transition on the transform, so the compositor runs it
  and the main thread wakes only at the ends, on a timer per list. Nothing is
  animated from `requestAnimationFrame`.
- The track is still snapped to whole stops, so each end of the scroll rests
  on whole names. There is no "Page 2 of 4" while scrolling.
- The first descent opens where paging would: with the train's next stop near
  the top (the stop it last left above it). After that it runs from the top.
  A new route, which includes a stop being left on a full journey, starts
  again from there.
- `renderCallingPoints`, the `ResizeObserver` and the page timer all call
  `paintScroll`, which leaves a leg alone unless the room or the stop height
  has changed. When one has, the same leg carries on from wherever the column
  has got to. `glide` reads that position **before** setting
  `transition: none`: a style read after it cancels the leg and returns where
  it was going, which made the route leap to the foot whenever the reason line
  appeared mid-scroll.
- `configure` runs on every render pass, so it restarts the lists only when
  one of the three values has actually changed.

---

# Addendum 10 — Looking a station up by name in /admin

A station card used to want the CRS code and nothing else, which meant
knowing it or looking it up elsewhere. Each card now has a **Station** box
above the code: type a name or a code and it suggests stations, and a pick
fills in the CRS field.

## The list is shipped, not fetched

Neither feed has a station search, and a call per keystroke would be the
wrong thing to spend an allowance on anyway. `web/static/stations.json` holds
every National Rail station with a CRS code — 2,638, 74 KB, one to a line —
and `admin.js` fetches it once from `/static` and searches it in the page, so
the lookup costs nothing and works with no internet. Nothing in the app
rebuilds it.

- **Source.** NaPTAN, the rail area (910), as XML from
  `naptan.api.dft.gov.uk`. The CSV export is smaller (555 KB against 27 MB)
  but has no CRS column; the XML carries it on each stop point's
  `AnnotatedRailRef`. The API refuses `HEAD`.
- **Licence.** Open Government Licence v3.0, which asks for attribution: the
  file carries it in its `licence` field, and so does the README.
- **Rebuild** with `python -m describer.stationlist` (downloads) or
  `--source FILE`, then commit. It is a deliberate act, like recording a
  fixture. It refuses to write fewer than 2,000 stations, so a broken download
  cannot replace the list with a stub.
- **Parsing** (`parse_naptan`): active stop points with a three-letter CRS,
  "Rail Station" / "Railway Station" / "Station" stripped from the name. 29
  codes have more than one stop point — Clapham Junction has five, and SGB is
  both "Smethwick Galton Bridge" and its "High Level" — so each keeps its
  shortest name. Names are NaPTAN's, not Darwin's: "London Kings Cross"
  without the apostrophe, "Abbey Wood (London)" for ABW beside a separate
  ABX "Abbey Wood".
- `tests/fixtures/naptan_910_sample.xml` is ten stop points cut from the real
  file, with every locality list but the first removed (Paddington's alone was
  3,397 `NptgLocalityRef`s and 530 KB). `test_stationlist.py` also checks the
  committed list's shape.

## The box

- **The CRS field is still what is saved.** The Station box has no `name`, so
  `readForm`'s sweep never sees it, and `readStationList` reads only the
  fields it names. A pick writes the code into the CRS field and dispatches
  `input` and `change` from there, exactly as typing it would: the page marks
  itself unsaved and a profile's editor commits its draft.
- **The box's own `input` and `change` stop at the box.** Searching changes
  nothing that is saved, and the form marks itself unsaved on any `input`
  that reaches it.
- It always shows the name of whatever code the CRS field holds. Leaving it
  without a pick, or pressing Escape, puts that name back. A three-letter code
  the list does not know is flagged under the field and still saved: the list
  is NaPTAN's, and Darwin knows codes it does not (SPX, for one).
- **Ranking** (`searchStations`): the code itself, then names starting with
  the text, then codes starting with it (under three letters), then every
  typed word starting a word of the name ("lon bri"), then the text anywhere.
  Shorter names first within each, so Sydenham precedes Sydenham Hill. A lone
  "x" is "cross". Eight suggestions at most.
- A combobox by ARIA's pattern: `aria-expanded`, `aria-controls`,
  `aria-activedescendant`, arrows to move, Enter to pick — never to submit the
  form from here — and a suggestion chosen on `pointerdown`, since a click
  would blur the box first and close the list under the pointer.
- `stationCard` wires it, so the profile editor's cards have it too. Each
  listbox gets its own id.

---

# Addendum 11 — One structural colour becomes a role after all

Addendum 5 called `--tl-bar` — the background of thameslink's "Later trains"
header — structure, not palette, and left it out of the vocabulary. Asked for
directly, it turns out to be exactly the kind of thing the palette exists for:
a colour someone wants to change without touching a stylesheet. It becomes an
eighth role, `bar`, present only on `thameslink`.

- `ThemeColoursConfig` (`config.py`) gains `bar: Colour | None = None`,
  alongside the other seven. Every other theme's `colours` dict can carry it
  too — the model is still shared — and every other theme still ignores it,
  the same way `thameslink` already ignores a hand-written `on_time`.
- `themes/colours.js`'s `ROLES` maps it to `--tl-bar`; `thameslink.js`'s own
  `ROLES` list is the only one that includes it, so `applyColours` never
  writes it anywhere else.
- **The admin page no longer builds its colour rows from one shared list
  filtered per theme.** `bar` is thameslink-only and nothing else will ever
  share it, so `admin.js` keeps a `ROLE_LABELS` map (every role's name) and a
  `THEME_ROLES` map that names, per theme, exactly the roles and the order to
  show them in — `modern`'s list is the original seven, `thameslink`'s drops
  `on_time` and appends `bar` at the end. `buildColourFields` just walks a
  theme's own list.
- **Its contrast reads against the text colour, not the background.** Every
  other role is checked against `--bg`, because that is what sits behind it;
  the bar is its own ground, and it is `--fg` painted on top of it that has
  to stay legible. `syncColours` special-cases `role === 'bar'` to check
  against `colours.text` instead.
- The mock board in `/admin` grows a "Later trains" strip in the chosen
  colour, but only for a theme whose `effectiveColours()` actually has a
  `bar` — `renderPreview` checks for it before adding the markup, so `modern`
  is unaffected.

## Out of scope

`--tl-rule` (the hairline) stays structural — nobody asked for it, and it is
a `color-mix()` of `accent`, not a flat colour, so it was never a plain
palette entry like `--tl-bar`. The other themes' bars, flaps and casings are
untouched.

---

# Addendum 12 — Each calling point's expected time

`display.show_calling_times` (default **off**), the Darwin "next train" style:
a stop reads "Reading (19:29)" rather than plain "Reading". Off by default
because a time on every stop is characters a page no longer has for stops —
see the truncation history in Addendum 3, which this addendum does not
reopen. No parser or model change and no new API call: `CallingPoint` already
carried `scheduled_time`, `expected_time` and `actual_time` for both feeds:
this only reads them.

## The time rules

`stopTime(point)` in `board.js`, in order:

1. `cancelled` → `null` — no time is printed at all, the same as a bare name
   today. A cancelled stop cannot arrive, so a time on it would be a lie.
2. `actual_time` set → that time. The train has already left this stop, and
   the actual time is the truest thing on offer.
3. `expected_time` an `"HH:MM"` string → that time, and `late` is set when it
   reads later than `scheduled_time` (a plain string compare, since both are
   zero-padded "HH:MM"). This is RTT's only non-null case, and Darwin's own
   estimate once it has one.
4. `expected_time` is `"On time"`, `"Delayed"`, or there is no estimate at
   all → `scheduled_time`, not marked late. "Delayed" means Darwin has
   nothing better to offer, and the booked time is the only honest fallback;
   printing nothing would be worse than printing the time the train was
   supposed to call.

`late` only ever comes from comparing two known clock times (case 3). A
stop that falls back to its booked time (case 4) is never marked late, even
though the train carrying it is by definition not on time — there is no
second time to compare against, so nothing is asserted.

## Where each theme puts it

- **modern, crt, 1990s** — board.js's own `renderCallingPoints` builds
  `"Name (HH:MM)"` once, before pagination, so the default pager's cache key
  (which already includes the joined text) picks up a changed estimate on
  its own. This is the same list of strings paginateCallingPoints has always
  paged; a longer line simply pages sooner.
- **splitflap, nse, led-matrix** — unchanged. All three take the `points`
  argument as opaque strings and flap, page or scroll whatever they are
  given, so `"Abbey Wood (19:47)"` is just a longer name to them. Confirmed
  by reading all three modules: none assumes a stop is a bare station name.
- **thameslink** — the one theme that does not want the time folded into
  the name, because it draws the name ellipsised against the line and the
  time would either be swallowed by that ellipsis or force the whole route
  narrower. board.js therefore does *not* bake the time into `points` for
  this theme's benefit specifically; instead `renderCallingPoints` gained a
  fifth argument, `showTimes` (`display.show_calling_times`, passed straight
  through), alongside the existing `rawPoints` and `service`. `routeStops`
  and `journeyStops` now take the raw stops directly (rather than the
  `points` array of names) and attach a `time: {text, late} | null` to each
  one — recomputing `stopTime` from a small copy kept in `thameslink.js`
  itself, since a theme module cannot import `board.js` (the dependency runs
  the other way). Each `.tl-stop` is now a flex row of `.tl-stop-name`
  (which keeps the ellipsis) and, when there is a time, a right-aligned
  `.tl-stop-time` in `--fg`, or `--late` when `late` is set. The stop's own
  `color` (its `--late`/`--fg`/`--muted` by state) no longer needs to apply
  to the time, because the time span sets its own colour explicitly rather
  than inheriting — both are still overridden together to `--muted` when the
  train above is cancelled, alongside the other cancelled-route rules.
  `__key` already serialises the whole stop list, so a stop's time changing
  repaints exactly as a station being left already did. `measure()`, the
  snap to whole stops, both paging and `scroll_route` all read the rendered
  row height, which the flex row keeps the same as the old block one, so
  none of the four needed touching — confirmed by hand rather than assumed.

## Reaching the option

`DisplayConfig.show_calling_times: bool = False` and the matching
`DisplayOverride.show_calling_times: bool | None = None`, beside
`show_position` — same mechanism, so a profile can turn it on for one window
of the day without touching the base config. It rides `/api/state` for
free: `poller.state()` dumps the whole resolved `DisplayConfig`, so a new
field needs nothing added there, exactly as `show_formation` did not. The
admin checkbox sits in Display → Screen next to "Show formation", and the
profile editor mirrors it in its three usual places (the rendered card, the
`readForm`-equivalent that builds `entry.display`, and `newProfile`'s
starting point) alongside `show_position` and `show_formation`.

## Verifying this by hand

Checked against a live RDM board (`sources.fallback: null`, `RTT_TOKEN`
unset — Addendum 4 stands; this needed no RTT call to verify) carrying a
real delayed-and-partly-cancelled service: the cancelled stops printed no
time on every theme, the delayed stops fell back to their booked time, and
splitflap, nse and led-matrix needed no code change to show any of it. All
seven themes were checked at 1920×1080 and 1280×720, one board and a split
screen with an arrivals board on the other half, with the option on and
off — off changes nothing, which is the point of the default. thameslink's
right-aligned time was confirmed to sit on one edge across every stop by
measuring `getBoundingClientRect()`, not by eye.

---

# Addendum 13 — Keys and updates from /admin

Two things that used to need SSH: changing an API key, and moving the Pi to
the latest release.

## Credentials, write-only

A **Credentials** block on the Data sources tab sets or clears `RDM_API_KEY`
and `RTT_TOKEN`. `describer/credentials.py` owns it.

- **Nothing ever reads a key back.** `/api/credentials` (GET) and the answer to
  its POST say whether each key is set and when it was last changed from
  /admin, and nothing more — no value, no last four characters. `/api/status`
  keeps its `credentials: {rdm, rtt}` booleans.
- **Where a key goes.** `/etc/describer/describer.env` whenever
  `/etc/describer` exists, because that is the file the unit reads *last*
  (Addendum 1): a key written anywhere else is overridden at the next restart.
  The repo `.env` otherwise, i.e. on a dev machine. `DESCRIBER_ENV_FILE`
  overrides both.
- **How it is written.** In place, mode 600, keeping every line that is not
  ours; a temp file cannot be renamed over it because the directory is
  root's. Values are single-quoted as `install.sh` writes them, and a value
  holding a quote, a newline or a control character is refused rather than
  escaped. A set key gets a `# RDM_API_KEY set from /admin at …` comment above
  it, which is where the "changed" time comes from; `install.sh` rewrites the
  file without them, which only loses the time.
- **Clearing deletes the line.** It never writes `RDM_API_KEY=''`: an empty
  assignment is still an assignment, and that is how the deployed key was once
  blanked.
- **Live, no restart.** The write updates `os.environ`, then
  `SourceManager.credentials_changed()` forgets its health verdicts and
  **drops both clients**. That matters for RTT: `RttClient` caches the access
  token bought with the old refresh token, and whether the token is a refresh
  token at all. The active source then goes back to the primary if it has a
  key, or to the fallback if only that one does.
- **Addendum 4 holds.** Without `/etc/describer`, an RTT token is refused
  (and the box is disabled) unless `DESCRIBER_ALLOW_RTT_TOKEN=1`. RDM keys may
  be set anywhere.
- The fields have no `name`, so `readForm` never sweeps a key into
  `config.yaml`; their `input`/`change` events stop at the field so the page
  is not marked unsaved; Enter in one saves the keys, never the form.
- LAN trust: anyone on the network can *replace* a key, though not read one.

## Updates from GitHub

`describer/updater.py`, the `updates:` config block, and an **Updates** block
on the Status tab. **Offered, never automatic.**

- **Checking** is `git fetch origin +refs/heads/main:refs/remotes/origin/main`
  — the repository is public, so no token and no GitHub API allowance. First
  60 s after startup (`STARTUP_DELAY`), then every `updates.check_interval`
  (default 21600 s, floor 600), or on **Check now**. It lists up to 50 commit
  subjects with an exact count, and never touches the working tree.
- **What blocks an update**, reported instead of offering the button: the
  checkout not on `updates.branch`, local changes to *tracked* files, or local
  commits the remote lacks. Untracked files do not block — `config.yaml`,
  `voices/`, `.piper/` and `.venv/` all live untracked in the checkout.
- **Installing** (`POST /api/updates/apply`): `git merge --ff-only` to the
  fetched commit; `pip install -r requirements.txt` only if that file changed;
  then a **smoke test** — a fresh interpreter imports `describer.main` and
  loads the live `config.yaml`. Any failure before the restart is `git reset
  --hard` back to the old commit (plus a reinstall if requirements had
  changed), and nothing restarts. A broken restart would take /admin down
  with it, and /admin is the only way back without SSH.
- **Restarting** is `systemctl --user restart --no-block describer.service`,
  a second after the answer goes out. Only under systemd (`INVOCATION_ID`);
  in dev the update stops at the install and says to restart by hand.
- **Before the restart, `Poller.close_streams()`** puts `None` in every SSE
  queue and the stream generator returns on it. An SSE stream never ends on
  its own, so without this the shutdown waits on the kiosk's connection until
  systemd's stop timeout kills it.
- **The board reloads itself.** Every state frame carries `version` (the short
  commit the process started on — fixed at startup, since HEAD moves before
  the code in memory does). `board.js` reloads the page when the stream comes
  back naming a different one, so the kiosk needs no restart and fetches the
  new files (`no-cache`). The admin page does the same from `/api/updates`.
- **`deploy/` is out of reach.** The units are copied into place by
  `install.sh` with sudo; when a release touches `deploy/`, /admin says to
  re-run it and still offers the rest of the update.
- `updates.remote` and `updates.branch` are passed to git, so their patterns
  forbid a leading dash. `updates` is not overridable by a profile — plumbing,
  like `sources`.

## Tests

`test_credentials.py` (file round trip, refusals, clearing, the dev guard,
the source manager adopting a new key) and `test_updater.py`, which builds a
bare "GitHub", a publishing clone and a "Pi" clone in `tmp_path` and never
touches the network: listing, every blocker, the `deploy/` flag, requirements
installed only when changed, rollback on a failed smoke test, and the restart
hook. The autouse `no_update_checks` fixture in `conftest.py` pushes
`STARTUP_DELAY` out of reach, so no app test ever fetches from GitHub.

## Out of scope

Automatic installs, release tags or channels, a changelog beyond commit
subjects, updating the unit files, and restarting the kiosk (the page reloads
itself instead).

---

# Addendum 14 — Splitflap flaps are blitted, not laid out

On the Pi the flip was too slow to pass for a machine. Each flap was a
`<span>` whose text changed every step, restarted through `element.animate`
on transform and opacity, so every step made Chromium re-lay-out the cell,
repaint the text run, promote the span to a compositor layer for 40 ms and
demote it again. Measured over CDP in a headless Chrome on the dev Mac
(1920×1080, dpr 1, main-thread time from the start of the frame's callbacks to
the timer that runs after its rendering work):

| | one board, 5 rows | two boards, 11 rows |
|---|---|---|
| DOM flaps | 4.5–5.3 ms a frame, frames dropped to 50–67 ms | not measured |
| canvas tiles | 0.3–0.5 ms a frame, no frame over 2 ms | 0.8–1.0 ms a frame |

A Pi 4B is eight to twelve times slower on one core than that Mac, so the old
figure is 40–60 ms a frame for one board — 15 to 25 fps while anything was
flipping, and worse with two boards. The new one is a few milliseconds.

## What changed

- **Every line of flaps is one `<canvas>`**, in the cell, the stops list or
  the reason line, sized by `splitflap.css` from `--chars` at the pitch the
  DOM flaps measured (0.86em tile, 0.08em gap, 1.32em tall). `TILE_EM` and
  `GAP_EM` in the module are the same numbers; change both. `--chars` is the
  stylesheet's for a board column and is set inline for a paged line, whose
  width is now computed from the host's font size and the pitch rather than
  measured off a rendered flap.
- **Tiles come from a sprite sheet**: the whole drum drawn once per font,
  size and colour, on its flap with the hinge line, and shared by every
  canvas that matches. A step is then three blits at most. The colours of
  the flap halves are `--flap-top`, `--flap-bottom` and `--flap-edge` in the
  stylesheet, read when a sheet is drawn.
- **The flip is three frames a step**, not a frame per refresh: the old top
  flap foreshortened against the hinge, the new bottom flap landing under
  it, then the tile at rest. At 25 steps a second a fourth frame is nothing
  the eye can see and would cost a scaled blit per flap per refresh. Reduced
  motion draws only the third.
- The stepping is still clock-paced with the watchdog behind it, the stagger,
  `MAX_STEPS`, the status alternation, the abbreviation table and the paging
  are unchanged, and `flap_ms` and `click_sound` mean what they did.
- A cell whose colour or face changes (a row turning cancelled) gets a fresh
  sheet and a full repaint; so does every canvas when `document.fonts`
  finishes loading or a `ResizeObserver` reports a new box, exactly as `nse`
  handles its dots.

## Verifying this by hand

`canvas.__to`, `__from`, `__remaining` and `__phase` are the per-flap state;
a flap at rest has `__remaining` of -1. Fixture names still prove little:
`Abbey Wood via Whitechapel` becomes `ABBEY WOOD` by the via rule, and
`London Charing Cross` becomes `LONDON CHARING X`. Checked at 1920×1080 and
1280×720, one and two boards: no canvas extends past its cell, no page
overflow, and a cancelled row's tiles come out in `--cancelled`. Not yet
judged on the Pi itself.

---

# Addendum 15 — Weather

A 24-hour forecast strip, from Open-Meteo (`https://api.open-meteo.com/v1/forecast`).
No key, no account, and a generous free allowance, so unlike the two rail
sources this needed no credential plumbing at all — the only upstream in the
project `.env` has nothing to say about.

## Decisions

- **`display.show_weather: bool = False`**, beside `show_calling_times`, in
  both `DisplayConfig` and `DisplayOverride`, so a profile can turn it on for
  one window of the day. Nothing is fetched while it is off in the *resolved*
  config, or while the display schedule has the screen off — `WeatherService`
  reads the same `is_display_on` check the poller does.
- **Only `modern` and `thameslink` draw it.** A theme opts in with
  `export const weather = true;`, exactly as `serviceDetail` works; every
  other theme gets the strip hidden — `display: none` — before a forecast is
  even asked for. Adding a theme still needs no backend change: the backend
  hands out a `weather` array parallel to `boards` regardless of who is
  listening.
- **Coordinates come from NaPTAN.** `stationlist.parse_naptan` now returns
  `(crs, name, lat, lon)` — 4 dp, plenty for a forecast — keeping the
  coordinates that came with whichever stop point's name was kept for a
  shared code. `stations.json` rows are `[crs, name, lat, lon]`; `admin.js`
  already read the first two by position, so the extra two cost it nothing.
  Eleven of the 2,638 committed stations have no fix at all (NaPTAN's own
  gap, not a parsing failure).
  `StationConfig.latitude`/`longitude` override it, both or neither, bounded
  to roughly the UK (lat 49–61, lon −9–2) — for a code Darwin knows and
  NaPTAN does not (SPX), or to point the forecast somewhere else entirely. No
  coordinates at all, from either source, means no forecast for that board:
  logged once at INFO (`WeatherService` remembers which CRS codes it has
  already said this about), never an error.
- **The backend reads the committed `stations.json` too**, lazily and once
  (`stationlist._coordinates`, an `lru_cache`'d dict keyed by CRS), so the
  list still has one home; a test resets the cache after monkeypatching
  `stationlist.OUTPUT`.

## `describer/weather.py`

Same split as the rail clients: a pure `parse_forecast(raw: dict) -> Forecast`
and a thin async `WeatherClient` around it.

- `condition_for(code: int) -> str` maps every WMO code Open-Meteo documents
  to the closed vocabulary the plan specified (`clear`, `partly-cloudy`,
  `cloudy`, `fog`, `drizzle`, `rain`, `heavy-rain`, `sleet`, `snow`,
  `showers`, `thunder`, `unknown`); `test_weather.py` parametrizes every one
  of them. An undocumented code reads `unknown` rather than raising — an
  icon is decoration, not a fact worth crashing a board over.
- `parse_forecast` trims to 24 hours and drops anything earlier than the
  `current` block's own time, defensively — `forecast_hours=24` is a request
  to the API, not a guarantee about what it sends back, the same posture the
  rail parsers take towards Darwin and RTT.
- **`WeatherService` runs its own loop**, independent of `Poller`'s: a slow
  or wedged board fetch must never hold up the weather, or the other way
  round. It is handed a `config_provider` (`store.active`) rather than the
  `ConfigStore` itself, so it always reasons about the *resolved* config —
  profile applied — exactly as the poller takes one snapshot per tick rather
  than reading the store twice.
  - Keyed by coordinates rounded to 2 dp (`location_key`), so Reading and a
    station a few streets over share one request.
  - `_poll_once` is `_run`'s body with the sleep pulled out, purely so a test
    can drive one pass without also waiting out `TICK_SECONDS` (30 s) in real
    time — the first version of this test suite took several minutes to run
    because every service-level test called the combined tick-and-sleep
    method directly.
  - Exponential backoff (the same `BACKOFF_STEPS` shape as the poller's) on a
    fetch failure, keeping the last good forecast; a forecast older than
    `weather.stale_after` (default 10800 s, floor 1800 s) is dropped by
    `forecast_for` rather than shown — weather that old is simply wrong, and
    unlike a board it carries no "stale" indicator to qualify it with.
  - `on_change` is called after a successful fetch so `main.py` can wire it
    to `poller.publish_now()` (new: `Poller._publish(self.state())` under a
    name other code may call) — a forecast refreshing on its own 30-minute
    clock must not wait for the next board poll to reach the browser.
- **`WeatherConfig` is plumbing**, like `sources` and `updates`: not
  profile-overridable. The on/off switch is `display.show_weather`, which is.

## Wiring

- `main.py`'s lifespan builds one `WeatherService`, starts and stops it
  beside the poller, and registers `poller.set_weather(weather.forecasts_for_state)`
  — the one call `Poller.state()` makes into weather, so the poller does not
  otherwise know it exists, matching the plan's instruction exactly.
  `api_put_config` calls `weather.update_config` and `weather.config_changed()`
  alongside the poller's own `config_changed()`.
- `Poller.state()` gains `weather`: a list parallel to `boards`, each entry a
  forecast dict or `null`, taken from the same resolved config snapshot the
  boards use.
- `/api/status` gains `weather: {enabled, locations, last_fetch, last_error}`.

## Tests

`test_weather.py` (parsing, every WMO code, `forecast_hours` trimming, a
missing `precipitation_probability`, the shared cache key, backoff keeping
the last good forecast, a stale one dropped, nothing fetched with the option
off or the schedule off, a station with no coordinates logged once and never
called, `httpx.MockTransport` for the client-level tests), `test_config.py`
(defaults, the refresh floor, lat/lon both-or-neither and bounds, a profile
turning the option on), `test_stationlist.py` (coordinates parsed from the
sample, `coordinates_for`), `test_app.py` (`/api/state` carries `weather`
parallel to `boards` via an injected fake provider; `/api/status` carries the
block). The autouse `no_weather_network` fixture in `conftest.py` poisons
`WeatherClient`'s *default* transport (an `httpx.AsyncBaseTransport` that
raises `ConnectError`) rather than the class's `fetch` method, so
`test_weather.py`'s own unit tests, which hand `WeatherClient` an explicit
`httpx.MockTransport`, are unaffected — the same reason `no_update_checks`
only pushes `STARTUP_DELAY` out of reach rather than disabling the updater
outright.

## Frontend

- `index.html`: `<div class="weather" hidden></div>`, last child of the board
  template, after `.messages`; a `<template id="weather-hour-template">` for
  each of the eight hourly cells.
- **The strip sits outside `.rows`.** Every other optional line
  (`.service-detail`, `.calling-points`, `.service-reason`) is moved *inside*
  `.rows` by `board.js`'s `placeAfter`, because each belongs to the top
  service and must ride under it. The weather strip belongs to the board as
  a whole, not to one train, so it stays a plain grid row of `.board` —
  `base.css`'s `grid-template-rows` gained a fifth track, `auto`, and every
  theme that defines its own template (`thameslink`, `1990s`, `nse`,
  `led-matrix`) gained one too, or reused a spare row (`thameslink` sits it
  directly above the clock/source row, which moved from row 5 to row 6).
- **`--weather-share` shrinks `.rows`'s own type a little**, the same
  mechanism as `--detail-share`, added to the shared `--slot` formula in
  `base.css`. But unlike the three lines above, it cannot make the strip's
  *own* height exact: `--slot` and `--row-font` are custom properties set
  (via `container-type: size`) on `.rows` itself, invisible to a sibling
  grid row. **This is a deliberate, documented gap from the plan's wording**
  ("claimed through `--weather-share`"): the strip's actual footprint is set
  in `base.css` with `vh` units, judged by eye against `--weather-share:
  1.6`, the same way `.messages` always has been. Unifying the two sizing
  systems would mean moving the strip inside `.rows`, which would then have
  to anchor it to a service the way the other three lines do — and it isn't
  one. Verified at both resolutions, one and two boards: `--slot` and the row
  height are provably unaffected for every theme that does not opt in
  (measured, not eyeballed): `crt` and `splitflap` show a literal `+ 0` in
  their computed `--slot` expression, and the other three never mention the
  term at all.
- **The strip always renders its full shape when the option is on**, even
  for a board with nothing to show (no coordinates, or nothing fresh yet) —
  every cell simply blank. This is what keeps a split screen's two halves
  the same height when one station has a forecast and the other does not,
  which the plan flagged as a risk for `thameslink`'s clock panel and
  "Later trains" bar specifically. Rendering the (empty) shape rather than
  measuring and syncing two real heights after the fact needed no new
  machinery — no `--weather-share` sync in `syncRowShares`, because the
  share is already identical on both boards (it depends only on the theme
  and `display.show_weather`, both global), and no per-board reservation,
  because the DOM structure — and so the rendered height — is identical
  whether or not this particular board's station has a fix. Confirmed by
  hand: one station given a real CRS and the other a CRS with no NaPTAN
  coordinates, split screen, both themes — the clock panels and the later-
  trains bar sit on the same line on both halves.
- **Icons are CSS masks over a colour**, one small data-URI SVG per
  condition in `base.css`'s `.wx-icon[data-condition="…"]` rules — the same
  recipe as the wheelchair sign in Addendum 8, and for the same reason: Pi OS
  Lite has no emoji font. Day/night (`is_day`) only changes `clear` (sun vs.
  moon) and `partly-cloudy` (sun-behind-cloud vs. moon-behind-cloud); every
  other condition looks the same lit by either.
- **Eight columns, not 24**: `WEATHER_STEP = 3` picks every third hour.
  Measured rather than assumed to need the plan's fallback to six at 720p
  split-screen — at both sizes, one board and two, eight three-hourly
  columns fit with no clipping (`scrollWidth - clientWidth`), so the
  container-query drop to six was not needed and was not built. A rain
  chance is shown only at or above `WEATHER_PRECIP_THRESHOLD` (20%), an
  eyeballed constant in the manner of `QUIET_BELOW`/`BUSY_FROM`.
- `renderWeather` keys the strip's rebuild on `JSON.stringify(forecast)`, so
  an unchanged forecast (most 30-minute refreshes, most poller ticks) never
  rebuilds the DOM.
- `modern`: a full-width band at the foot, `--weather-share` set to `1.6`,
  a hairline mixed like `--rule`, icons in `--accent`, and the *current*
  temperature only gets `--accent` at or below 0 °C (`data-freezing`) — the
  one place this theme lets a number itself carry colour, judged by eye
  against the plan's "may use `--accent`" latitude.
- `thameslink`: the strip sits directly above the clock panel
  (`grid-row: 6`, moved down from 5), deliberately outside `--slot` like the
  reason line — the route below can always turn one more page to give the
  height back. A thin `--tl-rule` hairline above it, `--fg` text, `--accent`
  icons and hour labels.

## Verified by hand

Dev server, `sources.fallback: null`, `RTT_TOKEN` blanked for the run (the
committed `.env` on this machine carries a real token; it was neither read
nor edited — the shell's own copy of the variable was set to an empty string
before `uvicorn` started, which `python-dotenv`'s `override=False` leaves
alone). `display.show_weather` turned on against real coordinates for a real
station reached the real Open-Meteo API once through `/api/config`, and
`/api/state`/`/api/status` carried a real forecast end to end — legitimate
under Addendum 4, which is about `data.rtt.io`'s metered allowance, not every
upstream the project has. Checked: `modern` and `thameslink` at 1920×1080 and
1280×720, one board and two (no clipping, no page overflow, both halves the
same height); the option toggled off (nothing rendered, `--weather-share: 0`,
`--slot` unchanged); all five other themes (`crt`, `splitflap`, `1990s`,
`nse`, `led-matrix`) with the option left on globally — the strip stays
`display: none` and every theme's own `--slot` computation is untouched. The
admin page: the Display tab's checkbox and hint, a station card's "Forecast
location" details block (collapsed hint correctly names NaPTAN or its
absence; the two number fields read and write `latitude`/`longitude`), and
the Data sources tab's Weather block (config fields plus live status,
including "2 locations tracked" once two stations shared a rounded key).

Judged on the Pi's own monitor after release, and it looks right there:
`--weather-share: 1.6` and the strip's `vh` height stand as they are.

## Out of scope

Per-station `show_weather` (it is a `DisplayConfig` toggle, like
`show_calling_times`), a forecast for anything but the top-of-hour "now" and
the 24-hour strip (no multi-day outlook), wind/humidity/UV (the strip has
room for temperature, condition and rain chance only), and announcing the
weather.
