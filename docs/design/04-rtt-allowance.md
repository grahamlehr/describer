# Addendum 4 — Never spend the RTT allowance on local work

The free RTT token allows **10 calls a minute, 100 an hour, 1000 a day**, and
it is one allowance shared by every machine holding the token. The Pi polling
two stations on the 120 s floor already spends about 60 board calls an hour,
so a dev session that reaches the live API does not just waste quota — it
takes the real board off RTT for the rest of the hour and burns the daily
budget by mid-afternoon. RDM has no comparable limit; RTT is the scarce one.

## The rule

**Local runs and tests never call `data.rtt.io`.** Every RTT behaviour is
already exercisable from `tests/fixtures/rtt_*.json`. Live RTT calls are for
the Pi, and for a deliberate capture the user has asked for.

In practice, on the dev Mac:

- Leave `RTT_TOKEN` unset in the dev shell and out of the repo `.env`. A
  source with no credentials is a permanent failure reported once at startup
  and never retried (Addendum 1), so this alone is enough to make live RTT
  calls impossible. It is the primary guard; the rest are belt and braces.
- Set `sources.fallback: null` in the local `config.yaml`, so a failing RDM
  key cannot quietly turn into RTT traffic.
- Never use the admin **Force source** control, `/api/source/force`, or
  `sources.primary: rtt` on a dev machine. Forcing bypasses the failover logic
  and puts every poll on RTT immediately.
- Never `curl`, `httpx` or otherwise poke `data.rtt.io` "just to see the
  shape". The shape is in `tests/fixtures/rtt_live_capture.json`, which exists
  for exactly that, and `test_rtt.py` parses it to catch drift.
- Never leave a dev server polling unattended. Even on RDM it is pointless;
  stop uvicorn when the check is done.

## Tests make no network calls at all

`pytest` must be runnable with the network down. Both clients keep a pure
`parse_board(...)` that works on recorded JSON precisely so this holds. A test
that needs an HTTP layer mocks the transport; it does not reach an upstream,
and it does not read `RTT_TOKEN` from the developer's environment. The autouse
`clean_credentials` fixture in `conftest.py` deletes `RDM_API_KEY` and
`RTT_TOKEN` for every test; the ones that need a key opt in to the
`rdm_credentials` / `rtt_credentials` fixtures, which set obvious fakes. Do not
undo that, and add any new credential variable to it.

If a test would need a live call to be meaningful, it does not belong in
`pytest`; record a fixture instead.

## Recording a new fixture

The one legitimate reason to spend live RTT calls. It is a deliberate,
user-approved act, not a step inside another task:

- Ask first, and say how many calls it will cost.
- One capture, one station, one window. Save the raw response verbatim to
  `tests/fixtures/`, strip the token from any header dump, and commit it.
- Do not loop, do not poll, do not re-run to "get a fresher one".

## If the allowance has already been spent

`/api/status` reports `rate_limit` from the `X-RateLimit-Remaining-*` headers,
and the admin Status block shows it. A `429` carries `Retry-After`. Nothing
resets it early — wait out the window. Do not register a second token to work
around a limit hit by local testing; fix the testing instead.
