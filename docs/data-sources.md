# Data sources and failover

_Moved from the old CLAUDE.md ("Data sources")._

Two independent upstreams behind one `RailSource` protocol
(`fetch_board(crs, mode) -> Board`). Each client has a pure
`parse_board(...)` that works on recorded JSON plus a thin async HTTP wrapper,
and both normalise into our own `Service` / `Board` dataclasses, so themes and
announcements never touch raw API shapes.

- **RDM (primary)**: Rail Data Marketplace **Live Arrival and Departure
  Boards** (Darwin LDBWS) over JSON REST, `x-apikey` header from
  `RDM_API_KEY`. One endpoint, `GetArrDepBoardWithDetails/{crs}`, serves both
  modes: departures read `std`/`etd` and `subsequentCallingPoints`, arrivals
  `sta`/`eta` and `previousCallingPoints`. The departures-only product has no
  arrivals operation; switching means changing `ldbws.BOARD_ENDPOINT` and
  `sources.rdm.base_url` together. Only RDM carries position and formation.
- **RTT (fallback)**: Realtime Trains next-generation API at
  `https://data.rtt.io`, bearer token from `RTT_TOKEN` (an access token or a
  refresh token; the client works out which). **Free tier: 10 calls a minute,
  100 an hour, 1000 a day, shared with the live Pi.** See the allowance rule
  below. (`docs/design/02`)
- **Failover** (`rail/sources.py`): the active source is global, so both
  halves of a split screen agree. After `failover_after` *consecutive*
  primary failures it switches to the fallback, and probes the primary every
  `recover_after` s; a primary success resets the count. Missing credentials
  are a permanent failure reported once. Failover never masks a stale board.
  `Board.source` says which feed served it. (`docs/design/01`, `/08`)
- Poll interval default 30 s, never below 20 s; while RTT is live, never below
  `sources.rtt.min_poll_interval` (120 s). Back off exponentially on errors and
  keep showing the last good board with a "data stale" indicator.
- Keys live in the environment or a **never-committed** `.env`. Never write a
  key into `config.yaml` or logs. `/admin` can set them, write-only
  (`docs/design/13`).
