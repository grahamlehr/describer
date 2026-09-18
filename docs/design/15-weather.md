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
local, uncommitted `.env` on this machine carries a real token; it was neither read
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
