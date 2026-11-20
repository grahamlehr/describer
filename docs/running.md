# Running and local dev

_Moved from the old CLAUDE.md ("Running")._

Local dev (Mac):
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml     # then set sources.fallback: null
export RDM_API_KEY=...
env RTT_TOKEN= uvicorn describer.main:app --reload --port 8080
```
From a Claude session, use `preview_start` with the `describer` entry, which
does the same. Board at `http://localhost:8080/`, settings at `/admin`.

To look at a theme without a key or any rail API, run the mock board
instead: `python3 scripts/mock_board.py` (or `preview_start` entry
`board-mock`), then `http://localhost:8090/`. See `docs/verifying-board.md`.

Before finishing:
```bash
env -u RTT_TOKEN -u RDM_API_KEY .venv/bin/python -m pytest -q
.venv/bin/ruff format . && .venv/bin/ruff check .
```

Pi: run `sudo deploy/install.sh` once, then `sudo systemctl status describer
kiosk shutdown-button`.
