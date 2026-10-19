# Never spend the RTT allowance on local work

_Moved from the old CLAUDE.md. Fixture rules: `tests/fixtures/rtt/README.md`._

The full rule is in `docs/design/04`. It is not optional.

- **Local runs and tests never call `data.rtt.io`.** Every RTT behaviour is
  exercisable from `tests/fixtures/rtt_*.json`; the live shape is in
  `rtt_live_capture.json`.
- Keep `RTT_TOKEN` unset in the dev shell. The local `.env` on this Mac *does*
  hold a real token; `.claude/launch.json` starts uvicorn with `RTT_TOKEN=`
  so python-dotenv (`override=False`) leaves it unloaded. Start a dev server
  through `preview_start`, or `env RTT_TOKEN= …` by hand, never bare.
- `sources.fallback: null` in the local `config.yaml`. Never use Force source,
  `/api/source/force`, or `sources.primary: rtt` on a dev machine.
- Never leave a dev server polling unattended; stop it when the check is done.
- `pytest` makes no network calls at all. `conftest.py` deletes the
  credentials for every test (opt in to the fake `rdm_credentials` /
  `rtt_credentials` fixtures), poisons the weather client's transport and
  keeps the updater from fetching. Add any new credential or upstream there.
- Recording a new fixture is the one legitimate live call: ask the user first,
  say how many calls it costs, capture once, commit it.
