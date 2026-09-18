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
                   # once nothing imports it (removed 2026-09, one release on)
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

The `rtt:` block above is the v1 shape and is superseded: Addendum 2 has the
current one (`data.rtt.io`, `detail_rows: 3`, `time_window`,
`min_poll_interval`).

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

- `deploy/install.sh` prompts for `RTT_TOKEN` alongside `RDM_API_KEY`
  and writes both to `/etc/describer/describer.env`, mode 600. Enter at a
  prompt keeps the value already there.
- The backend unit reads `%h/describer/.env` **before**
  `/etc/describer/describer.env`, not after. systemd applies environment files
  in order and the last assignment wins, an assignment to the empty string
  included, so a checkout holding a bare `RDM_API_KEY=` silently blanked the
  deployed key. RDM was then a permanent failure from the first tick, the board
  failed over to RTT, and a day's allowance went with it. Deployed credentials
  must be read last. `install.sh` copies the unit; changing it means re-running
  the script (or re-copying) plus `systemctl --user daemon-reload`.
- No new apt or pip dependencies; RTT's bearer token is a plain header.

## Out of scope for this addendum

Merging data from both sources at once, per-station source selection,
a third source, and using RTT service detail to enrich RDM boards.
