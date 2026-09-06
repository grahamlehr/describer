# Describer

A live UK National Rail departure board for a Raspberry Pi 4B on a 16:9
monitor. It polls the Darwin feed through the Rail Data Marketplace — falling
back to Realtime Trains when Darwin is unreachable — renders one or two
stations full screen in one of three themes, and speaks platform-style
announcements as trains approach.

```
┌──────────────────────────────┬──────────────────────────────┐
│ London Paddington  DEPARTURES│ Reading            ARRIVALS  │
│ 14:32  Bristol T M   9 On time  GW                          │
│ 14:36  Abbey Wood   12 Exp 14:51 XR                         │
└──────────────────────────────┴──────────────────────────────┘
```

## Requirements

- Python 3.11+
- A Rail Data Marketplace subscription to the **Live Departure Board
  (LDBWS)** product, which gives you an API key
- Optional but recommended: a free [Realtime Trains](https://api-portal.rtt.io)
  API token for the fallback source
- For announcements: [Piper](https://github.com/rhasspy/piper) and a British
  English voice
- On the Pi: Raspberry Pi OS Lite 64-bit, plus `cage` and `chromium-browser`
  (installed by `deploy/install.sh`)

## Local development (Mac or Linux)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env        # then put your credentials in it
export RDM_API_KEY=...      # or rely on .env
export RTT_TOKEN=...        # optional fallback source
uvicorn describer.main:app --reload --port 8080
```

- Board: <http://localhost:8080/>
- Settings: <http://localhost:8080/admin>

Announcements are skipped gracefully if Piper is not installed; the admin
page shows what is missing.

## Pi installation

```bash
git clone <this repo> ~/describer && cd ~/describer
./deploy/install.sh
```

The script installs system packages, builds the venv, fetches Piper and a
voice, and installs two systemd **user** services:

- `describer.service` — the FastAPI backend on port 8080
- `kiosk.service` — `cage` running Chromium in `--kiosk` against localhost

`install.sh` asks for both sets of credentials and writes them to
`/etc/describer/describer.env` (mode 600). To change them later, edit that
file and:

```bash
systemctl --user restart describer kiosk
systemctl --user status describer kiosk
journalctl --user -u describer -f
```

## Configuration

`config.yaml` is the source of truth (repo root in development,
`/etc/describer/config.yaml` on the Pi; override with `DESCRIBER_CONFIG`).
Every option is documented in `config.example.yaml`. The admin page at
`/admin` edits the same file and applies theme, station, feed and
announcement changes live — no restart.

Credentials are never stored in `config.yaml`. They come from the
environment (or `.env`): `RDM_API_KEY` for the Rail Data Marketplace, and
`RTT_TOKEN` for Realtime Trains. Neither is ever written to the logs.

### Data sources

Two independent upstreams serve the same board, so a Marketplace outage or an
expired key does not blank the screen:

```yaml
sources:
  primary: rdm            # rdm | rtt
  fallback: rtt           # rdm | rtt | null (null = no failover)
  failover_after: 3       # consecutive primary failures before switching
  recover_after: 300      # seconds between quiet retries of the primary
```

The active source is global, so both halves of a split screen always agree.
After `failover_after` consecutive primary failures the fallback takes over;
the primary is then retried quietly every `recover_after` seconds and taken
back on its first success. If the source that is live now fails, the board
goes stale exactly as it always did — failover never hides missing data.

Describer uses the Realtime Trains **next-generation** API at `data.rtt.io`
(the older `api.rtt.io` v1 service is closed to new sign-ups and is being
switched off). `RTT_TOKEN` may be a long-life access token or a refresh token;
the client tries the exchange at `/api/get_access_token` once and remembers
which it holds, renewing short-life tokens before they expire.

One `/rtt/location` call returns every service touching the station in a time
window, each carrying an arrival block, a departure block, or both — so
departures and arrivals are two readings of one response rather than two
endpoints. Calling points still cost one request per service, so only the
first `sources.rtt.detail_rows` rows get them (the board only ever expands the
first row).

**Mind the allowance.** A free RTT token permits 10 requests a minute and 100
an hour. The board therefore never polls faster than
`sources.rtt.min_poll_interval` (120 s by default) while RTT is live, drops the
optional calling-point calls once the allowance runs low (below 3 for the
minute or 25 for the hour) rather than losing the board itself, and shows what
is left in the `/admin` status block. A 30 s
poll across two stations would exhaust an hour's allowance in about four
minutes.

Compared with Darwin, RTT still has no NRCC disruption messages and no bare
"Delayed" state (a train with no estimate simply shows nothing), but the v2 API
does supply delay and cancellation reasons, train length and planned-versus-
actual platforms.

Each board shows which feed is live beside its clock, and `/admin` has a **Force source**
control (`auto` / `rdm` / `rtt`) for testing the fallback without pulling the
network cable. Forcing is held in memory only and is never written to
`config.yaml`.

Config files from v1 that still use the old `api:` block keep loading for one
release; the loader maps them onto `sources` and logs a deprecation warning.

### Stations

One or two. Two render side by side, each half a complete board. List the
same station twice with different modes to show its departures and arrivals
together:

```yaml
stations:
  - {crs: PAD, mode: departures, rows: 8, announce: true}
  - {crs: PAD, mode: arrivals,   rows: 8, announce: false}
```

### Themes

| Theme       | Look                                                        |
|-------------|-------------------------------------------------------------|
| `modern`    | Clean, high-contrast, dark. The default.                     |
| `crt`       | Amber or green phosphor, scanlines, curvature, blinking cursor. |
| `splitflap` | Solari mechanical board; characters flip to their target.    |

A theme is a CSS file in `describer/web/static/themes/` plus a same-named JS
module. The module may export `attach`, `configure`, `detach`, `renderText`
and `afterRender`, all optional; `board.js` calls them and hands over the
theme's own config block. `modern.js` is a no-op example to copy. Adding a
theme touches no backend code beyond adding its name to the `theme` literal
in `describer/config.py`, so the config validates.

### Announcements

Piper synthesises to a WAV cached on disk by a hash of the text, voice and
volume, so repeated wording costs one file read. Playback goes through
`aplay` (or `paplay`, or `afplay` in development), preceded by an optional
two-tone chime that the app generates itself.

Each service is announced at most once per event type (arriving, delayed,
cancelled). The record is in memory and resets on restart.

### Display schedule

With `schedule.enabled`, the board blanks outside the daily window, puts the
HDMI output to sleep (`wlr-randr`, or `vcgencmd` as a fallback), and stops
both polling and announcements until the next on-time. Per-weekday overrides
are supported; a day mapped to `null` stays off all day.

## How it fits together

```
LDBWS REST ──► rail/ldbws.py ─┐
                              ├─► rail/sources.py ──► Service/Board ──► rail/poller.py
RTT REST   ──► rail/rtt.py  ──┘   (picks one, fails over)  │      │
                                                           │      │
                                            announce/scheduler.py │
                                                     │            │
                                             announce/tts.py    SSE ──► board.js ──► theme
```

The poller owns the only copy of the live boards, backs off exponentially on
errors, and keeps serving the last good data with a "data stale" flag. The
browser never polls the API: it holds one SSE connection and receives whole
state snapshots.

## Tests

```bash
.venv/bin/python -m pytest
```

The data layer is tested against recorded LDBWS and RTT responses in
`tests/fixtures/`, so no network and no credentials are needed. `test_rtt.py`
checks the two parsers agree about the same train, and `test_sources.py`
covers failover and recovery.

Before finishing a change:

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
```

## Out of scope for v1

Destination filters, multi-Pi sync, mock data mode, authentication,
packaging for other users, portrait layout, non-UK data.
