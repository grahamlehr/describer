# Describer

A live UK National Rail departure board for a Raspberry Pi 4B on a 16:9
monitor. It polls the Darwin feed through the Rail Data Marketplace, renders
one or two stations full screen in one of three themes, and speaks
platform-style announcements as trains approach.

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
- For announcements: [Piper](https://github.com/rhasspy/piper) and a British
  English voice
- On the Pi: Raspberry Pi OS Lite 64-bit, plus `cage` and `chromium-browser`
  (installed by `deploy/install.sh`)

## Local development (Mac or Linux)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env        # then put your key in it
export RDM_API_KEY=...      # or rely on .env
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

Then put your key in `~/describer/.env` and:

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

The API key is never stored in `config.yaml`. It comes from `RDM_API_KEY`
in the environment or in `.env`, and is never written to the logs.

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
LDBWS REST ──► rail/client.py ──► Service/Board ──► rail/poller.py
                                                      │      │
                                       announce/scheduler.py │
                                                │            │
                                        announce/tts.py    SSE  ──► board.js ──► theme
```

The poller owns the only copy of the live boards, backs off exponentially on
errors, and keeps serving the last good data with a "data stale" flag. The
browser never polls the API: it holds one SSE connection and receives whole
state snapshots.

## Tests

```bash
.venv/bin/python -m pytest
```

The data layer is tested against recorded LDBWS responses in
`tests/fixtures/`, so no network or API key is needed.

Before finishing a change:

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
```

## Out of scope for v1

Destination filters, multi-Pi sync, mock data mode, authentication,
packaging for other users, portrait layout, non-UK data.
