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
