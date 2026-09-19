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
- **Restarting** is `systemctl restart --no-block describer.service` (system
  scope; polkit allows the `describer` user that one restart), a second after
  the answer goes out. When the release touched `deploy/` it is not this but
  the next section. Only under systemd (`INVOCATION_ID`);
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
- **`deploy/` is applied by a root oneshot**, described below. It used to be
  out of reach: /admin said to re-run `install.sh` with sudo. That step is gone.
- `updates.remote` and `updates.branch` are passed to git, so their patterns
  forbid a leading dash. `updates` is not overridable by a profile — plumbing,
  like `sources`.

## Applying `deploy/` after an update (Phase 3 of the easy-install work)

The units, the polkit rules and the shutdown-button watcher live in `deploy/`
and are copied into place by `install.sh provision`. An update that changes
them used to stop at "re-run install.sh"; on a Pi someone else runs, nobody
will. The backend cannot do it: it is the unprivileged `describer` user.

**The mechanism.** After a successful update where `deploy_changed` is true,
the updater starts `describer-apply-deploy.service` (`systemctl start
--no-block`) *instead of* restarting the backend. A polkit rule
(`deploy/polkit/50-describer.rules`) lets `describer` start exactly that unit
and nothing else. The unit is a `Type=oneshot` as root running
`/usr/local/sbin/describer-apply-deploy` (`deploy/apply-deploy.sh`), which
logs every step to the journal, runs `install.sh provision`, does a
`daemon-reload`, restarts `kiosk` and `shutdown-button` only if their installed
files changed, and restarts `describer` last. The oneshot's result, from
`systemctl show`, is what /admin reports: **System files updated**, or **System
files not applied: see the log**. Systemd forgets it at reboot, which is right:
by then the files are on disk.

If the unit cannot be started (a Pi installed before this existed has none),
the updater falls back to the direct restart so the new code comes up, and
records "System files were not applied". Run `sudo
/opt/describer/deploy/install.sh provision` once and every later update
applies its own.

### Threat model

Two facts set it. `/admin` has no login, so anyone on the LAN can press
**Update** (LAN trust, as everywhere). And `/opt/describer`, including its
`.git` and every file in `deploy/`, belongs to `describer`, because the backend
runs as that user and the updater writes there. Anything that root does with
that tree is done on the word of whoever can reach /admin, or of whatever has
code execution as `describer`.

So the root side takes **nothing** from the checkout except a commit id, and
checks even that:

1. It reads the commit `/opt/describer` is at, with `git rev-parse HEAD` and
   `safe.directory` given on the command line. That reads a ref: no hook and
   no config command (`core.fsmonitor`, `core.pager`) runs, and a test plants
   both to prove it. The answer must be 40 hex digits.
2. It keeps its own root-owned clone at `/var/lib/describer-deploy` (a sibling
   of `describer`'s home, not inside it), cloned from a URL **written in the
   script**, not read from the checkout's remote, and fetches it every time.
3. It refuses unless that commit is in the clone and is `main` or an ancestor
   of it. A commit on another branch of the same repository is refused too.
4. It runs `install.sh provision` **from the clone, at that commit**, in a
   clean environment (`env -i`), so every root action is code that came
   straight from GitHub `main`.

**What that guarantees.** A LAN attacker who presses Update can only cause code
already on GitHub `main` to be installed, never their own. A process running
as `describer` still cannot reach root through this path: it can write to
`/opt/describer` but nothing there is read as root. The script is installed
root-owned under `/usr/local/sbin`, outside anything `describer` can write, and
polkit lets `describer` start that unit and no other.

**What it does not.** (a) `describer` can name an *older* commit of `main`, so
a downgrade to a release with a known bug is possible; the attacker cannot
choose code, only a point in main's history. (b) If GitHub `main` is bad, the
Pi installs it: the repository owner's account and the `updates.remote`
setting are the trust root. (c) A LAN attacker can restart the backend and
the board by pressing Update when one is waiting, which they could do anyway.
(d) Shut down and Restart in /admin are open to the same LAN, like everything
there.

**`provision` had to change to make (2) and (4) true.** Run as root from
somewhere else, the Phase 1 version still did four things that trusted the
checkout: it installed unit files from `$TARGET_DIR/deploy`, ran the venv's
`pip` and `python3` as root, extracted the Piper tarball as root with `tar -C`
into a tree `describer` could have put a symlink in, and `curl -o` wrote a
voice file through whatever symlink `describer` left at that name. Any one of
them is root for whoever edits `/opt/describer`. Now it installs every file
from `$SCRIPT_DIR` (the checkout it runs from, which on an update is the root
clone), and everything that lives *inside* `/opt/describer` (venv, pip, tar,
the voice download, the config seed) runs through `as_describer`
(`runuser -u describer`). Root does one recursive `chown -R` in its life, on a
tree it has just cloned itself. `provision` is otherwise safe against an
existing install: it never overwrites `config.yaml`, `describer.env` or the
checkout (each is guarded by "if absent"), and refuses if a `config.yaml` sits
in the checkout.

**The restart and the SSE streams.** The in-app restart calls
`Poller.close_streams()` first. The oneshot restarts the backend minutes
later, when it cannot, and the kiosk would only reconnect if it did earlier. So
`describer.service` now runs uvicorn with `--timeout-graceful-shutdown 3`.
Measured on a stand-in SSE app: without it an open stream kept uvicorn alive
for over 20 s after SIGTERM (systemd would wait 90); with it, it exits at
once.

## Power from /admin

`POST /api/system/poweroff` and `/reboot`, from **Status → Power**, each behind
a confirm dialog. The backend runs `systemctl poweroff|reboot` as `describer`;
polkit grants `login1.power-off`, `power-off-multiple-sessions`, `reboot` and
`reboot-multiple-sessions` for that user. `-ignore-inhibit` is not granted: if a
shutdown inhibitor on the Pi refuses the request, /admin shows the error and
the six-press button (root, no polkit) remains.

- Without `/etc/describer` (any dev machine) the endpoints answer 409, "Only on
  the Pi", and do nothing. `describer/system.py` runs the command through one
  injectable function, and `conftest.py` replaces it for every test with a
  tripwire that records the call and refuses, so no test can power off a Mac.
  `test_system.py` asserts the tripwire list is empty after the 409 tests.
- The command runs a second after the answer, as with a restart, so the page
  gets its reply. One request at a time; a refusal clears the pending state so
  the button can be tried again.

## Tests

`test_system.py` (the power controls, with fakes only), `test_apply_deploy.py`
(the polkit grants, the static rules on both scripts, and `apply-deploy.sh`
actually run against a local bare repository standing in for GitHub, with
`systemctl` stubbed: a good commit, an older one, one not in GitHub, one on
another branch, a checkout with a hostile remote, `install.sh`, `fsmonitor` and
`pager`, and a failing `provision`), and in `test_updater.py` the oneshot
started when `deploy/` changed and a direct restart when it did not.

`test_credentials.py` (file round trip, refusals, clearing, the dev guard,
the source manager adopting a new key) and `test_updater.py`, which builds a
bare "GitHub", a publishing clone and a "Pi" clone in `tmp_path` and never
touches the network: listing, every blocker, the `deploy/` flag, requirements
installed only when changed, rollback on a failed smoke test, and the restart
hook. The autouse `no_update_checks` fixture in `conftest.py` pushes
`STARTUP_DELAY` out of reach, so no app test ever fetches from GitHub.

## Out of scope

Automatic installs, release tags or channels, a changelog beyond commit
subjects, and restarting the kiosk after an update (the page reloads itself
instead; only a changed kiosk unit restarts it, via apply-deploy).
