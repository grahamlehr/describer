# RDM fixtures (Rail Data Marketplace, Darwin LDBWS)

Rules for anything touching the RDM Live Arrival and Departure Boards API:

- **Tests and exploratory runs never call RDM live.** It is keyed with `RDM_API_KEY` and rate-limited.
- Replay recorded JSON through the pure `parse_board(...)` in `describer/rail/ldbws.py`; mock the HTTP wrapper at the client boundary. `tests/conftest.py` deletes credentials for every test; opt in to the fake `rdm_credentials` fixture.
- Recording a new fixture needs Graham's OK and a stated number of calls. Capture once, remove the key and any headers, then commit it.
- Only RDM carries position and formation, so keep at least one fixture with both (`lbg_arrdep_formation.json`).
- `POST /api/setup/test-key` makes one real request by design; tests must stub it.

Current files live one level up (`pad_departures.json`, `pad_arrdep.json`, `rdg_arrivals.json`, `lbg_arrdep_formation.json`); this PR moves nothing, and new RDM fixtures should go in this folder.
