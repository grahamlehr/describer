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
git clone <this repo> && cd describer
sudo ./deploy/install.sh
```

The install is root-run and idempotent, and does everything: system packages,
a `describer` system user, the checkout at `/opt/describer`, the venv, Piper
and a voice, config and credential files under `/etc/describer`, and three
systemd services. It runs in two parts if you want them separately
(`sudo ./deploy/install.sh provision` then `configure`); `all` (the default)
runs both.

- `describer.service` — a **system** service, `User=describer`, running the
  FastAPI backend on port 8080 from `/opt/describer`
- `kiosk.service` — a **system** service on tty1 running `cage` and Chromium
  in `--kiosk` against localhost, also as `describer`. It has to be a system
  service: cage needs a logind seat for the display and input devices, and a
  lingering user session never gets one.
- `shutdown-button.service` — a **system** service watching a button for the
  clean-shutdown gesture (below).

### Moving from an older install

An install from before this layout kept the checkout at `~/describer` and ran
`describer.service` as a user unit for whoever owned it. Running
`sudo ./deploy/install.sh` (as that same user, via `sudo`) detects that old
layout automatically and migrates it: it copies `~/describer/config.yaml` to
`/etc/describer/config.yaml` (only if the new path is empty — it never
overwrites one already there), copies `voices/` and `.piper/` across to save
re-downloading them, chowns the existing `/etc/describer/describer.env` to
the new `describer` user, and disables the old per-user unit. It does not
delete `~/describer`; delete it yourself once the board is confirmed working
under the new layout.

### Shutdown button

Cutting mains power on a running Pi risks the SD card, so a momentary
normally-open button gives a safe "off" with no keyboard or network. Wire one
leg to **GPIO21 (pin 40)** and the other to **GND (pin 39)**; the internal
pull-up means no resistor. **Six presses within 10 seconds** runs
`systemctl poweroff`. Wait for the green ACT LED to stop flashing before
pulling the power. There is no abort once the sixth press lands, and testing
it means cycling power to bring the Pi back.

**No button? Use the page.** **/admin → Status → Power** has **Shut down** and
**Restart**, each behind a confirmation. It does the same clean poweroff (or
reboot), and needs no wiring. Anyone on your network who can open `/admin` can
press them, the same trust as the rest of that page. On a development machine
they answer "Only on the Pi" and do nothing. The button on GPIO21 is
unchanged and still works when the network does not.

The watcher is `deploy/shutdown_button.py`, installed to
`/usr/local/bin/shutdown-button` and run by the system Python with the apt
`python3-gpiozero`/`python3-lgpio` (not the venv). Only one process can own
GPIO21, so any other gesture on that button belongs in the same script.

```bash
sudo journalctl -u shutdown-button -n 5   # "watching GPIO21 for 6 presses within 10s"
```

`sudo deploy/install.sh configure` asks for both sets of credentials and
writes them to `/etc/describer/describer.env` (mode 600, owned by
`describer`). On a re-run, pressing Enter at a prompt keeps the credential
already there. To change them later, edit that file and:

```bash
sudo systemctl restart describer kiosk
sudo systemctl status describer kiosk shutdown-button
sudo journalctl -u describer -f
sudo journalctl -u kiosk -f
```

`describer.service` reads only `/etc/describer/describer.env` — a system
install has no checkout `.env` for it to read at all, which removes a whole
class of the "blanked key" trap a dev-machine `.env` could once cause. If
credentials appear to be missing, `/api/status` reports `credentials`, and
this settles it:

```bash
sudo systemctl show describer -p Environment | tr ' ' '\n' | grep -c '^RDM_API_KEY=.\+'
```

Keys can also be set or cleared from **/admin → Data sources → Credentials**,
which writes them to `/etc/describer/describer.env` and puts them to work
without a restart. It is write-only: a saved key is never shown again.

The unit files are *copied* into `/etc/systemd/system/`, so a bare `git pull`
does not update them. **Updating from /admin does** (below); only a hand-run
`git pull` leaves them behind, and `sudo /opt/describer/deploy/install.sh
provision` catches them up.

### Updating a Pi

The board checks GitHub every six hours (`updates:` in the config). When a
newer release is waiting, **/admin → Status → Updates** lists it, and **Update
and restart** installs it: it fast-forwards the checkout, installs
requirements if they changed, checks the new code starts with your config,
and restarts the backend (system scope: `systemctl restart describer.service`).
The board reloads itself when it comes back. If the new code will not start,
the checkout is put back and nothing restarts.

It will not update a checkout with local changes to tracked files, one on
another branch, or one with commits of its own.

**When a release changes `deploy/`** (the unit files, the polkit rules, the
shutdown-button watcher), there is nothing to re-run. After the update the
backend starts `describer-apply-deploy.service` instead of restarting itself,
and that root oneshot installs the new files and restarts what changed,
including the backend. **/admin → Status → Updates** then reads **System files
updated**, or **System files not applied: see the log** if it failed:

```bash
sudo journalctl -u describer-apply-deploy
```

That service does not take your checkout's word for what to install, since
the checkout belongs to the unprivileged `describer` user. It reads only the
commit the checkout is at, and installs from its own clone of the public
GitHub repository at `/var/lib/describer-deploy`, and only if that commit is on
GitHub's `main`. So a page anyone on your network can open can make the Pi
install what is on GitHub, never anything else. The reasoning is in
`docs/design/13-credentials-and-updates.md`.

A Pi that was installed before this existed does not have that service yet,
so the update that brings it in restarts the backend directly and says **System
files were not applied**. Run `sudo /opt/describer/deploy/install.sh provision`
once, and every update after that applies its own.

By hand:

```bash
cd /opt/describer && sudo -u describer git pull
sudo systemctl restart describer kiosk
```

Static files are served with `Cache-Control: no-cache`, so Chromium checks
every file with the server and picks up new themes on the reload. A Pi that
was last updated before that header existed may still be holding an old file
its browser decided was fresh, which shows up as a new theme drawn with the
previous release's stylesheet. Clearing the browser cache once fixes it for
good:

```bash
sudo systemctl stop kiosk
sudo -u describer rm -rf /var/lib/describer/.cache/chromium
sudo systemctl start kiosk
```

## Configuration

`config.yaml` is the source of truth. It is looked up as `config.yaml`
relative to the working directory first, then `/etc/describer/config.yaml`;
`DESCRIBER_CONFIG` overrides both. The backend runs with `WorkingDirectory`
set to the checkout (`/opt/describer` on the Pi), so **a `config.yaml` there
would win over `/etc/describer/config.yaml`** — `install.sh` refuses to
provision if one exists at `/opt/describer/config.yaml` for exactly this
reason. Check which file is live via `/api/status`'s `config_path` before
editing either.

Every option is documented in `config.example.yaml`. The admin page at
`/admin` edits the same file and applies theme, station, feed and
announcement changes live — no restart. What the file says and what is on the
screen can differ: see [Profiles](#profiles).

Each station card in `/admin` has a **Station** box: start typing a name or a
code ("sydenham", "lon bri", "kings x", "CLJ") and it suggests stations,
filling in the CRS code when you pick one. A code typed straight into the CRS
field still works, and one the list does not know is flagged rather than
refused. The list is `describer/web/static/stations.json` — every National
Rail station with a CRS code, about 2,600 of them, taken from NaPTAN. It is
committed and searched in the page, so the lookup costs no API calls and
works with no internet; nothing fetches it from anywhere at runtime. When
stations open or close, rebuild it by hand and commit the result:

```bash
python -m describer.stationlist
```

The station list contains public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

Credentials are never stored in `config.yaml`. They come from the
environment (or `.env`): `RDM_API_KEY` for the Rail Data Marketplace, and
`RTT_TOKEN` for Realtime Trains. Neither is ever written to the logs.

### First run: the setup screen and `/setup`

A board that has no Rail Data Marketplace key has nothing to show, so it says
so. The TV shows **Set up your departure board**, a QR code, and the address
in large type (`describer.local:8080/setup`, with the numeric address beneath
it, in case `.local` names do not resolve on your network). Scan the code with
your phone's camera; the phone has to be on the same Wi-Fi as the board.

`/setup` takes four short steps:

1. **Station.** Type a name and pick it, as in `/admin`. Choose trains leaving
   or trains arriving, and add a second station if you want one beside it.
2. **Rail data key.** A numbered walkthrough for getting the key: create an
   account at [raildata.org.uk](https://raildata.org.uk), find the product
   called **Live Arrival and Departure Boards** (not the departures-only one,
   which cannot show arrivals), subscribe, and copy the *Consumer key*.
   **Test key** makes one request with it for your station and tells you the
   next train, or why the key was not accepted. Nothing is saved until the
   last step.
3. **Sound.** Announcements on or off, through the TV (HDMI) or the 3.5 mm
   socket, with a **Play a test** button that plays through the choice you have
   made, not the one already saved.
4. **Finish.** Saves the key and the station, and the board appears on the TV
   within seconds, with no restart and no reload.

The screen also comes back if the Marketplace answers *401* or *403* to the key
(revoked, expired, or a mistake), with the heading **Your rail data key was
not accepted**. Saving a new key clears it at once, and it comes straight
back if that key is refused too. It
appears only for the Marketplace: a board with no Realtime Trains token is
normal. Polling is never stopped by any of this, and if the fallback source is
carrying a live board the screen stays out of the way.

The page stays available after setup, and `/admin` links to it. Like the
credentials block in `/admin`, the key is write-only: once saved it is never
shown again, and on a second visit the page says *A key is saved. Paste a new
one only to replace it.* Stations you already have keep their other options
(rows, platforms, walking time); everything else lives in `/admin`.

On a development machine with no `RDM_API_KEY` the screen shows too; that is
the setup screen working, not a fault. The QR code is served at
`/api/setup/qr.svg` and encodes the address by number when the board has one.

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

If the station is a walk away, `walk_time` hides the trains you could not get
to in time:

```yaml
stations:
  - crs: ABW
    mode: departures
    walk_time: 10           # minutes to the platform; 0 (the default) is off
```

A ten-minute walk means a train leaving in nine minutes is not a train — it is
a row pushing the 07:42 you could still make off the bottom of the board. The
cut is made against the time the train is actually expected, so one running
twenty minutes late reappears on the board, and it is re-made on every refresh
rather than baked into the stored board. A train whose time the feed does not
give is left on, and so is one showing a bare "Delayed": its booked time has
gone but the train has not, and nothing says when it will.

On an arrivals board it reads the same way, hiding trains arriving too soon to
meet.

Announcements move with it. A walk time larger than `announcements.lead_time`
would otherwise mean a train is dropped from the board before it is ever close
enough to be called, so the announcement comes at whichever of the two is
longer — the last moment the train is still worth walking for.

### Weather

`display.show_weather: true` adds a 24-hour forecast strip from
[Open-Meteo](https://open-meteo.com/), which needs no key and no account.
Only the **modern** and **thameslink** themes draw it; every other theme
ignores the option. The forecast location comes from NaPTAN's own coordinates
for the station's CRS code — the same list `/admin`'s Station box searches —
or from `latitude`/`longitude` set on the station itself, for a code Darwin
knows and NaPTAN does not:

```yaml
stations:
  - crs: SPX
    latitude: 51.4841   # both or neither; overrides NaPTAN's own coordinates
    longitude: -0.1245
display:
  show_weather: true
weather:
  refresh_interval: 1800   # seconds between fetches for a wanted location (min 900)
  stale_after: 10800       # a forecast older than this is dropped rather than shown
```

Two stations at the same or a neighbouring location share one request, and
nothing is fetched while the option is off or the display is scheduled off.
Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

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
module. Every export is optional — hooks such as `attach`, `configure`,
`detach`, `renderText`, `statusText`, `renderCallingPoints`, `renderReason`
and `afterRender`, plus the `serviceDetail` and `weather` flags that opt a
theme in to the position/formation line and the forecast strip. `board.js`
calls them and hands over the theme's own config block; the full contract is
the table in [`docs/design/03`](docs/design/03-board-layout-and-theme-contract.md#theme-module-contract). `modern.js` is the smallest example to copy: no animation at
all, so the module exists only to apply the palette. Adding a theme touches no
backend code beyond adding its name to the `theme` literal in
`describer/config.py`, so the config validates.

Two files in that directory are not themes. `dotmatrix.js` is the 5x7 dot font
and the text fitting that `nse` and `led-matrix` both draw with, kept in one
place so the letters cannot drift apart; `colours.js` is the only thing that
writes a configured colour on to the page.

Most themes give every service the same row. `thameslink` does not: it spends
the top of the board on one train and its calling points, and packs the rest
into a shorter list beneath a bar, which is why its rows and its stops take
shares of the height rather than a slot each. It also counts down — "6 min",
"Due" — instead of printing an estimate, so a delayed train alternates the
countdown with the time it is now expected.

There is no build step, and static files are served with `Cache-Control:
no-cache`, so an edited theme takes effect on the next page load.

#### Colours

`modern` and `thameslink` take their palette from the config, under seven
role names shared by every theme that has them:

```yaml
display:
  themes:
    modern:
      colours:
        background: "#06080c"
        text: "#f2f5f8"
        dim_text: "#8d97a5"
        accent: "#60a5fa"
        on_time: "#4ade80"
        late: "#fbbf24"
        cancelled: "#f87171"
```

A role left out keeps whatever the theme's own stylesheet says, so the CSS
stays the source of truth and a board whose owner once opened the picker still
gets a later redesign. Hairlines and the selected-row wash are mixed from these
rather than set separately, so they stay in step. `thameslink` has no
`on_time`: it counts down in the text colour.

The other themes are deliberately not configurable. Their colours are not a
palette — `crt` has a phosphor, `splitflap` has flaps, `1990s` has the seven
Teletext colours by name, and the two dot themes have one colour of dot — so
renaming those into roles would say something untrue about them. The `/admin`
Display tab draws a mock row in the chosen colours and reports the contrast of
each against the background, marking anything under 4.5:1. It still saves: it
is your board.

#### Why a train is late

Both feeds carry the reason as text, and the board prints it on one line
beneath the top service and its calling points:

> The 15:24 to Abbey Wood via Whitechapel is delayed due to a fault with the
> signalling system

The board is describing its top service, so that is whose reason this is; when
that train is running normally and a later one is not, the later one takes the
line. Either way the sentence names its train, because a board is read from
across a platform, where a bare "Delayed due to…" over a list of eight trains
says nothing useful. Only the reason matching the state the train is actually
in is shown — a service running to time may still be carrying the reason it was
late an hour ago. It is a
sentence rather than a column, so no theme trusts it to fit: most page it with
the stops every 5 s, `nse` pages it across the dots, and `led-matrix` scrolls
it. The line costs the board no height on the many days nothing is wrong.

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

### Profiles

The board can run a different station, theme and set of options at different
hours. A profile is a named window of the week (`start`, `end`, `days`)
carrying only the keys it changes; everything else comes from the config
above, which is also what runs when nothing matches. Entries are tried top to
bottom and the first match wins, so an overlap is settled by the order.

```yaml
profiles:
  enabled: true
  entries:
    - name: Morning rush
      days: [mon, tue, wed, thu, fri]
      start: "06:30"
      end: "09:30"
      stations: [{crs: ABW, rows: 10}]   # replaces the list, never merges
      display: {theme: thameslink}       # a key left out keeps the base value
      announcements: {lead_time: 180}
```

The `/admin` **Profiles** tab edits them, draws the week as a ribbon so gaps
and overlaps can be seen, and the Status tab can pin one so the evening board
can be checked at eleven in the morning. A profile may set `stations`,
`display` (bar `resolution`) and `announcements` (bar Piper's paths);
`sources`, `schedule`, `updates`, `weather` and `display.resolution` cannot be
overridden: they are plumbing and hardware, not presentation. The
display schedule above still owns the power — while the screen is off, no
profile is active and nothing is fetched.

## How it fits together

```
LDBWS REST ──► rail/ldbws.py ─┐
                              ├─► rail/sources.py ──► Service/Board ──► rail/poller.py
RTT REST   ──► rail/rtt.py  ──┘   (picks one, fails over)  │      │
                                                           │      │
                                            announce/scheduler.py │
                                                     │            │
                                             announce/tts.py    SSE ──► board.js ──► theme
                                                                  ▲
Open-Meteo ──► weather.py (own loop) ─────────────────────────────┘
```

`profiles.py` sits in front of all of it: the poller, the announcer and the
weather loop read the config *resolved* for the current hour, while `/admin`
reads and writes the raw file.

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
