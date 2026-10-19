# Describer — UK rail departure board for a Raspberry Pi

<!-- Rules only, under 200 lines. Status, history and long rationale live in docs/ (see Doc map).
     CLAUDE.md is a one-line stub (@AGENTS.md) so Claude and Codex read the same rules. -->

## What this is
A live departure board for one or two UK National Rail stations, shown full-screen on a 16:9
monitor attached to a Raspberry Pi 4B. It pulls live data from Rail Data Marketplace (Darwin
LDBWS), falling back to Realtime Trains, renders it in one of seven themes, and can speak
platform-style announcements (Piper TTS) as trains approach.

Personal project for a single Pi. Optimise for simplicity and reliability over portability or
packaging. No multi-user, no auth beyond LAN trust. Read the design note for an area
(`docs/design/`) before changing it; the traps recorded there cost real time to find.

## Stack
- Python 3.11+, FastAPI + uvicorn, `httpx.AsyncClient`, pydantic; deps in `requirements.txt`, venv at `.venv`.
- Frontend: plain HTML/CSS/JS served by FastAPI, no build step, no npm. Live updates by SSE.
- TTS: Piper (local), cached by text hash. Config: YAML (`config.yaml`) plus `/admin`.
- Pi OS 64-bit Lite, systemd system units run as the `describer` user, Chromium kiosk under `cage`.
- Detail: `docs/hardware-and-stack.md`, `docs/data-sources.md`, `docs/project-layout.md`.

## Commands
| Task | Command |
|---|---|
| Install | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |
| Dev | `env RTT_TOKEN= .venv/bin/uvicorn describer.main:app --reload --port 8080` (or `preview_start` entry `describer`) |
| Test | `env -u RTT_TOKEN -u RDM_API_KEY .venv/bin/python -m pytest -q` |
| Lint / format | `.venv/bin/ruff format . && .venv/bin/ruff check .` |
| Deploy | Graham's job (no SSH key from the Mac). The Pi self-updates from GitHub `main` (`docs/design/13`); first install is `sudo deploy/install.sh` on the Pi |

Python: use `.venv/bin/python`, never system `python` or pip. Run the tests and both ruff commands before finishing any change. Run `scripts/install-hooks.sh` once per clone (gitleaks pre-commit hook).

## Environments
| Env | URL / host | How to reach it | Notes |
|---|---|---|---|
| Local | `http://localhost:8080` (`/admin`, `/setup`) | Dev command above | `sources.fallback: null` in local `config.yaml` |
| Pi (prod) | `http://describer.local:8080` | Graham deploys; `/api/status` over HTTP says what is running | **Protected**: the live board. Config `/etc/describer/config.yaml`, secrets `/etc/describer/describer.env` (root-owned, possibly read-only; `<ask Graham>`) |
| Staging | `<ask Graham>` | none known | |

- `main` is what the Pi runs. **Never push to `main`**; work on a branch and open a PR. Merge only with Graham's OK.
- Config lookup: `DESCRIBER_CONFIG`, then a checkout `config.yaml`, then `/etc/describer/config.yaml`. `install.sh` refuses to provision if `/opt/describer/config.yaml` exists.
- Development is on a Mac, deployment on the Pi. Keep the app runnable on both (no Pi-only imports at module load).

## Invariants
- **Secrets**: `.env` (gitignored) or `/etc/describer/describer.env` on the Pi. Never commit, print or log them; `.env.example` lists every variable without values. Never write a key into `config.yaml`. Never log an API key or a full raw response at INFO.
- **Never spend the RTT allowance on local work**: see External APIs and `docs/rtt-allowance.md`. Not optional.
- **Offline**: the board must work with no internet. Fonts are self-hosted in `web/static/fonts`, never a CDN. The Pi must boot to the kiosk with no network.
- **Pi-friendly**: no heavy Python deps (no pandas, numpy). Animate transform/opacity only, or draw on a canvas; avoid CSS filters that force full-screen repaints.
- **Hardware**: 16:9, target 1920x1080 and also right at 1280x720, landscape only. HDMI audio by default, 3.5 mm jack selectable.
- **Shutdown button** (GPIO21 to GND, six presses in 10 s) lives in `deploy/shutdown_button.py`: its own system service on system Python, outside the app and venv. Any further gesture on that button goes in that script. `gpiozero` is imported only in `main()`. `conftest.py` replaces `Power` with a tripwire so no test can power off the machine.
- **Cache**: `/static` and both pages answer `Cache-Control: no-cache`. The kiosk is never hard-refreshed, so nothing may sit in its cache unchecked.
- **Config**: every option has a default in `config.example.yaml` with a one-line comment. `sources`, `schedule`, `updates`, `weather` and `display.resolution` are never profile-overridable. `ConfigStore.get()` is the raw file (use it to write); `active()` is the resolved config (use it to read). Mixing them writes a profile into the base file.
- **Credentials**: an empty assignment still assigns, so clear a key by deleting its line, never by writing it blank. `/admin` and `/setup` keys are write-only; no `/api/setup/*` route may log, echo or return a key. Setup never overwrites a saved key with a blank.
- **A 401 from RDM never stops polling**; it only changes what the setup screen says.
- **Deploy path**: `deploy/` is applied by the root oneshot `describer-apply-deploy` from its own clone, never from the checkout. `provision` reads files from `$SCRIPT_DIR`, never `$TARGET_DIR`, and runs anything in `/opt/describer` through `as_describer`. A unit file not copied by `install_units` is not applied by an update. `install.sh` runs under `set -e`: end helpers in an `if`, not `[ … ] && …`.
- **Lifecycle**: an SSE stream never ends on its own, so `Poller.close_streams()` must run before a restart.
- **Frontend**: ES modules, no bundler, no dependencies. Themes share one DOM and data model; adding a theme touches no backend code except `ThemeName` in `config.py`. Raw API shapes never reach themes or announcements (normalise into `Service` / `Board`).
- **Logging**: stdlib `logging` to stdout. Type hints everywhere; pydantic for config and API payloads.
- **Traps and two-place values**: read `docs/traps.md` before touching board layout, themes, the poller, admin form, setup screen or deploy. The "values that live in two places" table there must be updated in every home.

## External APIs
| API | Cost / limits | In tests |
|---|---|---|
| RTT (Realtime Trains, `data.rtt.io`, `RTT_TOKEN`) | Free tier 10/min, 100/hr, 1000/day, **shared with the live Pi**. Poll never below 120 s while RTT is live | Fixtures `tests/fixtures/rtt_*.json`; rules in `tests/fixtures/rtt/README.md`. Never called live |
| RDM (Rail Data Marketplace, LDBWS, `RDM_API_KEY`) | Keyed, rate-limited; poll default 30 s, never below 20 s | Fixtures `tests/fixtures/*arrdep*.json` and similar; rules in `tests/fixtures/rdm/README.md`. Never called live |
| Open-Meteo (weather) | Free, no key | Fixture `openmeteo_forecast.json`; the weather transport is poisoned in tests |

**Tests and exploratory runs must never call these live.** `pytest` makes no network calls: `conftest.py` deletes credentials for every test and poisons the weather client; add any new credential or upstream there. Mock at the client boundary.
- Keep `RTT_TOKEN` unset in the dev shell. The Mac's `.env` holds a real token; `.claude/launch.json` starts uvicorn with `RTT_TOKEN=`. Start a dev server through `preview_start` or `env RTT_TOKEN= …`, never bare.
- Never use Force source, `/api/source/force` or `sources.primary: rtt` on a dev machine. Never leave a dev server polling unattended; stop it when the check is done.
- Recording a new fixture is the one legitimate live call: ask Graham first, say how many calls it costs, capture once, commit it.

## Doc map
- `docs/HANDOFF.md`: current state and the prompt for the next session (`/handoff`)
- `docs/DECISIONS.md`: decisions already made (including out of scope). Don't relitigate them.
- `docs/design/`: one note per addendum, the why; index in `docs/design-notes-index.md`. Newer notes beat older ones; this file beats both.
- `docs/features.md`: boards, themes, announcements, schedule and profiles, `/setup`, `/admin`
- `docs/hardware-and-stack.md`: hardware, shutdown button, system layout, kiosk, config location
- `docs/data-sources.md`: RDM, RTT, failover, poll intervals
- `docs/project-layout.md`: the file tree
- `docs/conventions.md`: coding conventions
- `docs/rtt-allowance.md`: the full RTT allowance rule
- `docs/traps.md`: bugs that have happened, and values that live in two places
- `docs/running.md`: local dev and Pi commands
- `docs/verifying-board.md`: how to check a board change by hand (no frontend test harness)
- `docs/STATUS.md`, `docs/HISTORY.md`: status and field notes (create when needed)

## Definition of done
1. Tests pass and `ruff format` / `ruff check` are clean.
2. A feature lands with its docs in the same PR: `config.example.yaml`, README (user-facing), this file if it adds a rule or changes a command, and a new numbered note in `docs/design/` (decision, reason, numbers measured, trap found, how checked).
3. Board changes are checked by hand per `docs/verifying-board.md` (both resolutions, one and two stations, every theme touched).
4. The PR says where it was verified: local, device or prod.
5. Use `/ship` to commit and open the PR, and `/tidy` to clean up afterwards.
