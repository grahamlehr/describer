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

> **Superseded in part.** RDM is now the *primary* of two sources. See
> the addendum at the end of this file for the second source (Realtime
> Trains), the source abstraction, and automatic failover. Where the two
> sections disagree, the addendum wins.

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
    base_url: https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120
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
