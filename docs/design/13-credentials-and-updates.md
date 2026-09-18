# Addendum 13 — Keys and updates from /admin

Two things that used to need SSH: changing an API key, and moving the Pi to
the latest release.

## Credentials, write-only

A **Credentials** block on the Data sources tab sets or clears `RDM_API_KEY`
and `RTT_TOKEN`. `describer/credentials.py` owns it.

- **Nothing ever reads a key back.** `/api/credentials` (GET) and the answer to
  its POST say whether each key is set and when it was last changed from
  /admin, and nothing more — no value, no last four characters. `/api/status`
  keeps its `credentials: {rdm, rtt}` booleans.
- **Where a key goes.** `/etc/describer/describer.env` whenever
  `/etc/describer` exists, because that is the file the unit reads *last*
  (Addendum 1): a key written anywhere else is overridden at the next restart.
  The repo `.env` otherwise, i.e. on a dev machine. `DESCRIBER_ENV_FILE`
  overrides both.
- **How it is written.** In place, mode 600, keeping every line that is not
  ours; a temp file cannot be renamed over it because the directory is
  root's. Values are single-quoted as `install.sh` writes them, and a value
  holding a quote, a newline or a control character is refused rather than
  escaped. A set key gets a `# RDM_API_KEY set from /admin at …` comment above
  it, which is where the "changed" time comes from; `install.sh` rewrites the
  file without them, which only loses the time.
- **Clearing deletes the line.** It never writes `RDM_API_KEY=''`: an empty
  assignment is still an assignment, and that is how the deployed key was once
  blanked.
- **Live, no restart.** The write updates `os.environ`, then
  `SourceManager.credentials_changed()` forgets its health verdicts and
  **drops both clients**. That matters for RTT: `RttClient` caches the access
  token bought with the old refresh token, and whether the token is a refresh
  token at all. The active source then goes back to the primary if it has a
  key, or to the fallback if only that one does.
- **Addendum 4 holds.** Without `/etc/describer`, an RTT token is refused
  (and the box is disabled) unless `DESCRIBER_ALLOW_RTT_TOKEN=1`. RDM keys may
  be set anywhere.
- The fields have no `name`, so `readForm` never sweeps a key into
  `config.yaml`; their `input`/`change` events stop at the field so the page
  is not marked unsaved; Enter in one saves the keys, never the form.
- LAN trust: anyone on the network can *replace* a key, though not read one.

## Updates from GitHub

`describer/updater.py`, the `updates:` config block, and an **Updates** block
on the Status tab. **Offered, never automatic.**

- **Checking** is `git fetch origin +refs/heads/main:refs/remotes/origin/main`
  — the repository is public, so no token and no GitHub API allowance. First
  60 s after startup (`STARTUP_DELAY`), then every `updates.check_interval`
  (default 21600 s, floor 600), or on **Check now**. It lists up to 50 commit
  subjects with an exact count, and never touches the working tree.
- **What blocks an update**, reported instead of offering the button: the
  checkout not on `updates.branch`, local changes to *tracked* files, or local
  commits the remote lacks. Untracked files do not block — `config.yaml`,
  `voices/`, `.piper/` and `.venv/` all live untracked in the checkout.
- **Installing** (`POST /api/updates/apply`): `git merge --ff-only` to the
  fetched commit; `pip install -r requirements.txt` only if that file changed;
  then a **smoke test** — a fresh interpreter imports `describer.main` and
  loads the live `config.yaml`. Any failure before the restart is `git reset
  --hard` back to the old commit (plus a reinstall if requirements had
  changed), and nothing restarts. A broken restart would take /admin down
  with it, and /admin is the only way back without SSH.
- **Restarting** is `systemctl --user restart --no-block describer.service`,
  a second after the answer goes out. Only under systemd (`INVOCATION_ID`);
  in dev the update stops at the install and says to restart by hand.
- **Before the restart, `Poller.close_streams()`** puts `None` in every SSE
  queue and the stream generator returns on it. An SSE stream never ends on
  its own, so without this the shutdown waits on the kiosk's connection until
  systemd's stop timeout kills it.
- **The board reloads itself.** Every state frame carries `version` (the short
  commit the process started on — fixed at startup, since HEAD moves before
  the code in memory does). `board.js` reloads the page when the stream comes
  back naming a different one, so the kiosk needs no restart and fetches the
  new files (`no-cache`). The admin page does the same from `/api/updates`.
- **`deploy/` is out of reach.** The units are copied into place by
  `install.sh` with sudo; when a release touches `deploy/`, /admin says to
  re-run it and still offers the rest of the update.
- `updates.remote` and `updates.branch` are passed to git, so their patterns
  forbid a leading dash. `updates` is not overridable by a profile — plumbing,
  like `sources`.

## Tests

`test_credentials.py` (file round trip, refusals, clearing, the dev guard,
the source manager adopting a new key) and `test_updater.py`, which builds a
bare "GitHub", a publishing clone and a "Pi" clone in `tmp_path` and never
touches the network: listing, every blocker, the `deploy/` flag, requirements
installed only when changed, rollback on a failed smoke test, and the restart
hook. The autouse `no_update_checks` fixture in `conftest.py` pushes
`STARTUP_DELAY` out of reach, so no app test ever fetches from GitHub.

## Out of scope

Automatic installs, release tags or channels, a changelog beyond commit
subjects, updating the unit files, and restarting the kiosk (the page reloads
itself instead).
