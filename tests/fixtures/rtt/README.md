# RTT fixtures (Realtime Trains)

Rules for anything touching `data.rtt.io`:

- **Tests and exploratory runs never call RTT live.** The free tier is 10 calls a minute, 100 an hour and 1000 a day, shared with the live Pi, so a test run spends the board's allowance.
- Replay recorded responses; mock at the client boundary (`describer/rail/rtt.py`). `tests/conftest.py` deletes credentials for every test; opt in to the fake `rtt_credentials` fixture.
- Keep `RTT_TOKEN` unset in the dev shell. Never use Force source or `sources.primary: rtt` locally.
- Recording a new fixture is the one legitimate live call: ask Graham first, state how many calls it costs, capture once, then commit it. Strip tokens, headers and anything personal before committing.
- Name recorded files `rtt_<what>.json`; the live response shape is in `rtt_live_capture.json`.

Current files live one level up, as `tests/fixtures/rtt_*.json` (this PR moves nothing; new RTT fixtures should go in this folder). Full rule: `docs/rtt-allowance.md` and `docs/design/04-rtt-allowance.md`.
