# Features

_Moved from the old CLAUDE.md ("Features"): boards, themes, announcements, schedule and profiles, first run, admin page._

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

### First run (`/setup`)
- A board with no RDM key has nothing to show, so the board says so: a
  full-screen `.setup` element (QR code, `<host>.local:8080/setup`, the LAN
  address beneath it, one line of instructions). A phone on the same Wi-Fi
  finishes setup on `/setup`, and the screen goes away with no reload.
- `describer/setup.py` owns the decision: `setup_state(poller, store)` is
  `None`, `{"reason": "no_key"}` or `{"reason": "key_rejected"}`, each with
  `url` and `ip_url`. It rides in `/api/state` and every SSE frame as `setup`.
  **Only RDM counts**: a missing RTT token never triggers it, and neither does
  a primary that is not RDM. A live board from another source keeps it away.
- `key_rejected` is RDM answering 401 or 403 and nothing having served since.
  `RailApiError.status` carries the HTTP status; `SourceManager` remembers
  whether RDM's last answer was a refusal (`rdm_auth_failed`), cleared by a
  board, or by `credentials_changed()`. **It changes what the screen says and
  nothing else: a 401 never stops polling.**
- `/setup` (`setup.html`/`.css`/`.js`, mobile-first, 375 px) is four steps:
  station (the combobox, in `stationsearch.js`, shared with `/admin`), the key,
  sound, finish. `POST /api/setup/test-key` makes **one** LDBWS request with a
  key passed in (never read from the environment, never logged, never retried)
  and answers `ok`, `rejected` or `unreachable`. `POST /api/setup/complete`
  validates everything first, then writes the key (`credentials.write`), then
  the config through **`ConfigStore.get()`/`set()`, never `active()`**, then
  `credentials_changed()`. A station already in the file keeps its other
  options; profiles are untouched.
- The QR code is `GET /api/setup/qr.svg` (segno; `no-cache`): black modules on
  nothing, drawn by the page through `mask-image` on a white panel, so it is
  dark on light whatever the theme. It encodes `ip_url` when there is one.
- The key field and the station search box have no `name`, and `/setup` has no
  generic form reader, so a key travels in exactly two requests.

### Admin page (`/admin`)
- Edits every config option with validation, saves to `config.yaml`, applies
  live without restart where possible. Shows API status, rate limit, last
  fetch, a test announcement button, Force source / Force profile (memory
  only), credentials (write-only), updates from GitHub (a release that touches
  `deploy/` is applied by a root oneshot, no re-running `install.sh`) and
  Shut down / Restart. LAN-only; no login.
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
  The combobox is `stationsearch.js`, shared with `/setup`; the admin page
  wraps it, so a change there is a change to both pages. Its header link
  opens `/setup`.
