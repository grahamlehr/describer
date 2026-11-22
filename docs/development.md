# Development

## Local development (Mac or Linux)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env        # then put your RDM key in it
export RDM_API_KEY=...      # or rely on .env
env RTT_TOKEN= uvicorn describer.main:app --reload --port 8080
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
