# Describer

A live UK National Rail departure board for a Raspberry Pi 4B on a 16:9
monitor. It polls the Darwin feed through the Rail Data Marketplace — falling
back to Realtime Trains when Darwin is unreachable — renders one or two
stations full screen in one of seven themes, and speaks platform-style
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
- A Rail Data Marketplace subscription to the **Live Arrival and Departure
  Boards (LDBWS)** product, which gives you an API key. The departures-only
  product will not do: it has no arrivals operation at all
- Optional but recommended: a free [Realtime Trains](https://api-portal.rtt.io)
  API token for the fallback source
- For announcements: [Piper](https://github.com/rhasspy/piper) and a British
  English voice
- On the Pi: Raspberry Pi OS Lite 64-bit, plus `cage` and `chromium`
  (installed by `deploy/install.sh`). Current Pi OS has no `chromium-browser`
  package; the binary is `/usr/bin/chromium`

## Local development (Mac or Linux)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env        # then put your RDM key in it
export RDM_API_KEY=...      # or rely on .env
uvicorn describer.main:app --reload --port 8080
```

Leave `RTT_TOKEN` **unset** in development and set `sources.fallback: null` in
your local `config.yaml`. The RTT free tier is 1000 calls a day shared by every
machine holding the token, so anything a dev session spends is taken off the
live board. RDM has no comparable limit and is the only upstream a dev machine
should call; every RTT behaviour is exercisable from `tests/fixtures/rtt_*.json`.

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
voice, and installs two systemd services:

- `describer.service` — a **user** service running the FastAPI backend on
  port 8080
- `kiosk.service` — a **system** service on tty1 running `cage` and Chromium
  in `--kiosk` against localhost. It has to be a system service: cage needs a
  logind seat for the display and input devices, and a lingering user session
  never gets one.

`install.sh` asks for both sets of credentials and writes them to
`/etc/describer/describer.env` (mode 600). To change them later, edit that
file and:

```bash
systemctl --user restart describer
sudo systemctl restart kiosk
systemctl --user status describer
sudo systemctl status kiosk
journalctl --user -u describer -f
sudo journalctl -u kiosk -f
```

`describer.service` reads `~/describer/.env` first and
`/etc/describer/describer.env` second. systemd applies environment files in
order and the last assignment wins — **including an assignment to the empty
string** — so the deployed credentials are read last and a stray `.env` in the
checkout cannot blank them. Do not reverse this. A repo `.env` holding nothing
but `RDM_API_KEY=` once left the board with no key at all: RDM was a permanent
failure from the first poll, the board failed over to RTT without complaint,
and a day's allowance went with it. If credentials appear to be missing,
`/api/status` reports `credentials`, and this settles where they got lost:

```bash
systemctl --user show describer -p Environment | tr ' ' '\n' | grep -c '^RDM_API_KEY=.\+'
```

Note that the unit file is *copied* into `~/.config/systemd/user/`, so a
`git pull` does not update it. Changing it means re-running `install.sh`, or
re-copying it and running `systemctl --user daemon-reload`.

### Updating a Pi

```bash
cd ~/describer && git pull
systemctl --user restart describer && sudo systemctl restart kiosk
```

Static files are served with `Cache-Control: no-cache`, so Chromium checks
every file with the server and picks up new themes on the reload. A Pi that
was last updated before that header existed may still be holding an old file
its browser decided was fresh, which shows up as a new theme drawn with the
previous release's stylesheet. Clearing the browser cache once fixes it for
good:

```bash
sudo systemctl stop kiosk && rm -rf ~/.cache/chromium && sudo systemctl start kiosk
```

## Configuration

`config.yaml` is the source of truth. It is looked up as `config.yaml`
relative to the working directory first, then `/etc/describer/config.yaml`;
`DESCRIBER_CONFIG` overrides both. The backend runs with `WorkingDirectory`
set to the checkout, so **a `config.yaml` in `~/describer` on the Pi wins over
`/etc/describer/config.yaml`** — check which one `/api/status` reports as
`config_path` before editing either.

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

From the Marketplace, Describer calls one operation:
`GetArrDepBoardWithDetails/{crs}` on the **Live Arrival and Departure Boards**
product. It returns every service touching the station, so departures read
`std`/`etd` with the subsequent calling points and arrivals read `sta`/`eta`
with the previous ones — both modes from one response and one subscription. A
service missing the mode's time (it terminates or originates at that station)
is dropped by the parser. `sources.rdm.base_url` is the product URL up to the
API version, with **no operation on the end**; the client appends its own.

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

**Mind the allowance.** A free RTT token permits 10 requests a minute, 100 an
hour and 1000 a day. The board therefore never polls faster than
`sources.rtt.min_poll_interval` (120 s by default) while RTT is live, drops the
optional calling-point calls once the allowance runs low (below 3 for the
minute or 25 for the hour) rather than losing the board itself, and shows what
is left in the `/admin` status block. At the 120 s floor two stations spend
about 60 calls an hour on boards. A 30 s poll would instead issue four board
calls a minute, spending the hour's hundred in about 25 minutes, and the
calling-point calls on top of those breach the ten-a-minute limit sooner
still.

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

A station may also be narrowed to particular platforms, which is what you
want when the board is watching the two platforms you actually use:

```yaml
stations:
  - crs: RDG
    mode: departures
    platforms: ["7", "8"]     # empty (the default) shows every platform
    show_unplatformed: false  # true keeps trains with no confirmed platform
```

Matching ignores case and spacing, so `2a` finds platform 2A. Both feeds
withhold a platform until it is confirmed — LDBWS omits it, and RTT reports
only `actual` or `forecast` — so a filtered board carries fewer trains than
its row count until each is given its platform. Set `show_unplatformed: true`
to keep those trains on the board in the meantime, at the cost of showing
some that will turn out to be someone else's platform.

The filter is applied where the boards are handed out, not where they are
fetched, so the whole board stays in hand: widening the filter from `/admin`
shows the extra trains without waiting for another API call. Announcements
follow the filter too — a train filtered off the board is not announced.

### Themes

| Theme       | Look                                                        |
|-------------|-------------------------------------------------------------|
| `modern`    | Clean, high-contrast, dark. The default.                     |
| `crt`       | Amber or green phosphor, scanlines, curvature, blinking cursor. |
| `splitflap` | Solari mechanical board; characters flip to their target.    |
| `1990s`     | Ceefax-style Teletext page: white-on-blue bars, yellow times. |
| `nse`       | Network SouthEast sign over a flip-dot indicator; discs flip column by column and the stops turn a page at a time. |
| `led-matrix` | Amber LED dot-matrix panel of the 2000s; every word on the screen is lit dots, and long lines scroll. |
| `thameslink` | The LCD "next train" panels on the Thameslink core; one service takes the head of the board with its route drawn beneath it, the rest are a "Later trains" list counting down in minutes. |

A theme is a CSS file in `describer/web/static/themes/` plus a same-named JS
module. The module may export `attach`, `configure`, `detach`, `renderText`,
`statusText`, `renderCallingPoints` and `afterRender`, all optional;
`board.js` calls them and hands over the theme's own config block.
`modern.js` is a no-op example to copy. Adding a theme touches no backend
code beyond adding its name to the `theme` literal in `describer/config.py`,
so the config validates.

`dotmatrix.js` in that directory is not a theme. It is the 5x7 dot font and
the text fitting that `nse` and `led-matrix` both draw with, kept in one
place so the letters cannot drift apart.

Most themes give every service the same row. `thameslink` does not: it spends
the top of the board on one train and its calling points, and packs the rest
into a shorter list beneath a bar, which is why its rows and its stops take
shares of the height rather than a slot each. It also counts down — "6 min",
"Due" — instead of printing an estimate, so a delayed train alternates the
countdown with the time it is now expected.

There is no build step, and static files are served with `Cache-Control:
no-cache`, so an edited theme takes effect on the next page load.

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
