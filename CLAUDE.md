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

- Use the **Live Departure Board (LDBWS)** product from the Rail Data
  Marketplace (raildata.org.uk). It exposes Darwin data over a JSON REST
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
        fonts/             # self-hosted; bedstead.woff2 ships with the repo
        themes/
          modern.css      modern.js
          crt.css         crt.js
          splitflap.css   splitflap.js
          1990s.css       1990s.js
          nse.css         nse.js
          led-matrix.css  led-matrix.js
          dotmatrix.js    # the shared 5x7 dot font; not a theme
  deploy/
    install.sh             # Pi setup: apt deps, venv, piper, cage, services
    describer.service      # systemd *user* unit for the backend
    kiosk.service          # systemd *system* unit for cage + chromium
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
and `sudo systemctl status kiosk`.

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
| `renderCallingPoints(list, points)` | per board | replaces the default paging |
| `afterRender(boardsEl)` | after a pass | anything left over |

`statusText` and `renderCallingPoints` are new. A theme that wants to repaint
on a timer keeps the state itself and calls `api.render()`; `board.js` then
asks it again for the wording, so the timer and the render never disagree.

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
  every 6 s. The row width is *measured* from a rendered flap rather than
  assumed from the CSS, so it survives a font or size change.

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
  clock, stops label and messages straight into its own elements with no theme
  hook, so the stylesheet hides all five and `afterRender` mirrors each into a
  line of dots it owns (`.nse-ident`, `.nse-message`, `.nse-label`). The clock
  has no hook at all, so a timer reads `.clock` back every `CLOCK_MS`.
- The matrix therefore has five claims on its height: `--rows`,
  `--calling-share`, `--heading-share`, `--message-share` and `--ident-share`.
  `nse.js` sets `--message-share` per board, to 0.9 or 0. The message line is
  kept in the tree even when empty, because its `margin-top: auto` is what
  pins it and the identification line to the foot of a half-empty board.
- The watchdog must not be re-armed while one is pending. `scrollTick` calls
  `start()` every 45 ms, and pushing the deadline back each time meant it never
  fired — so when frames stopped, the stops froze at their first offset and the
  clock stuck part-swept.
- The stops and the message line turn a page at a time, never splitting a
  name. They cannot scroll: a disc is a fixed place on the board.
- A cell asked for before `nse.css` has arrived (`--chars` computes to the
  empty string) is queued and painted from `afterRender`; `board.js` will not
  ask again for text that has not changed.

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

`led-matrix` has no config block. Its field would have to be named
`led-matrix`, which is not a Python identifier, and amber is the only colour
those panels came in. The palette is two constants at the top of the module.

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

## Verifying this by hand

- The animation needs `requestAnimationFrame`, which a hidden browser pane
  does not run: the flaps freeze mid-alphabet. For a still, park them first:
  `for (const f of document.querySelectorAll(".flap")) { f.textContent = f.dataset.target; f.__nextAt = Infinity; }`
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
  That test reads a splitflap cell wrong: its text lives in flap children, so
  the cell never overflows. Sum the flap widths and the gaps instead, or just
  count them — a cell whose flap count is not the `--chars` for its field is
  the bug below, not a long name.
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
reading layout out of CSS has to run against a connected node.

## Open items

- Verified at 1280×720 and 1920×1080, one and two boards, all six themes:
  rows fill 100% of the height, no page overflow in either axis, and no cell
  clips except in `modern` and `crt` (see below). The model is width-bound at
  both sizes and the two are proportional, so 720p is not a separate case —
  what fits at 1080p fits at 720p, smaller.
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
