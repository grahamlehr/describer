# Plan: easy install for a non-technical user

Status: **agreed, not started.** Written 2026-09-18. When the work lands, the
reasoning moves into `docs/design/16-easy-install.md` and this file is deleted.

## The goal

> Open Raspberry Pi Imager → pick "Describer" → type your Wi-Fi → write the
> card → put it in the Pi → the TV says "Scan to set up" with a QR code → on
> your phone, pick a station and paste your Rail Data Marketplace key → the
> board appears.

No terminal, no SSH, no YAML, no IP address hunting. Nothing needs re-running
by hand after an update.

## Decisions already made (do not revisit)

- **Every user gets their own RDM key.** The key walkthrough is therefore the
  most important piece of user documentation in the project. No shared key,
  ever.
- **Graham's live Pi moves to the new layout** (`/opt/describer`, a
  `describer` system user). There is one layout, not two.
- **A ~1 GB image per release** is fine, built on GitHub's arm64 runners and
  attached to a GitHub Release.
- **The image is built in a separate repo, `grahamlehr/describer-image`.**
  It holds the pi-gen stage, the workflow and the Imager repository JSON. The
  app repo stays free of image-build machinery. The image build *calls* this
  repo's `deploy/install.sh`. There is one install script, run in two places.
- **RTT is left out of the first-run flow.** It stays an advanced option in
  `/admin`. An image ships with `sources.fallback: null`.
- The shutdown button on GPIO21 stays exactly as it is. The new `/admin` Shut
  down button is an addition, not a replacement.

## Out of scope for this plan

- A Wi-Fi hotspot or captive portal for when the Wi-Fi details are wrong. This
  may come later as its own addendum.
- A read-only root filesystem.
- Serving on port 80. The QR code makes typing `:8080` unnecessary.
- Any Pi model other than the 4B.

## Hardware gates (Graham's, not an agent's)

No agent can touch a Pi (see CLAUDE.md: no SSH key from the dev Mac). The
plan stops at each gate until Graham reports back.

| Gate | After | Graham does | Proves |
|------|-------|-------------|--------|
| **G1** | Phase 1 | Flashes plain Pi OS Lite (current release, arm64) to a **spare** SD card with Imager, clones the branch, runs `sudo ./deploy/install.sh`, and reports `/api/status` plus `sudo systemctl status describer kiosk`. | The new layout works on a fresh Pi. In particular, cage and Chromium run as a *system* user. |
| **G2** | G1 passes | Runs the migration on the live Pi. | Migration keeps config, credentials and the voice. |
| **G3** | Phase 2 | On the spare card: removes the RDM key and checks the TV shows the setup screen, then completes `/setup` from a phone. | The first-run flow works on a real TV and a real phone. |
| **G4** | Phase 3 | Clicks Shut down in `/admin`, and applies an update that touches `deploy/`. | polkit rules and the apply-deploy service work. |
| **G5** | Phase 4 | Flashes the built image with Imager through the custom repository. Sets Wi-Fi and hostname in Imager's settings and boots. | Imager applies its settings to our image. This is the riskiest assumption in the plan. |

---

## Phase 1: one system layout

**Repo:** describer. **Branch:** `easy-install-1-layout`. One PR.

### The layout

| What | Where | Owner |
|------|-------|-------|
| Checkout (a git clone of the public repo) | `/opt/describer` | `describer:describer` |
| venv, `.piper/`, `voices/` | inside the checkout, as now | `describer` |
| Config | `/etc/describer/config.yaml` | `describer`, mode 644. The dir is root-owned with 755, the file writable by `describer`. |
| Credentials | `/etc/describer/describer.env` | `describer`, mode 600 (unchanged path) |
| Service user home (Chromium profile, TTS cache if it lives in `~`) | `/var/lib/describer` | `describer` |

- `describer` is created with `useradd --system --home-dir /var/lib/describer
  --create-home --shell /usr/sbin/nologin`. It joins
  `video,render,input,audio,gpio`.
- **The checkout must never contain `config.yaml`.** It would win over
  `/etc/describer/config.yaml` (`CONFIG_PATHS` order). `install.sh` refuses
  to proceed if one exists in `/opt/describer`, and says why.
  - Don't change the `CONFIG_PATHS` order: the dev setup depends on it.
- Default the timezone to `Europe/London` if it is unset (`timedatectl`). The
  schedule, profiles and "due in" times all read local time.

### Units

- `deploy/describer.service` becomes a **system** unit:
  - `User=describer`
  - `WorkingDirectory=/opt/describer`
  - `ExecStart=/opt/describer/.venv/bin/uvicorn …`
  - `EnvironmentFile=/etc/describer/describer.env`
  
  **Drop the repo `.env` line.** On a system install nothing reads a
  checkout `.env`, which removes a whole class of the "blanked key" trap.
  Keep the comment explaining why the deployed file is the only one.
- `WantedBy=multi-user.target`.
- `deploy/kiosk.service`:
  - `User=describer`
  - `XDG_RUNTIME_DIR=/run/user/<describer uid>`, filled in by `install.sh`
    from `id -u describer`, as `@UID@` is today
  - add `After=describer.service`
  
  Keep everything else, especially the cursor settings and the curl wait.
- Unchanged: `shutdown-button.service`.
- No more `systemctl --user`, no more `loginctl enable-linger`.

### `install.sh` becomes root-run and two-part

Run as `sudo deploy/install.sh [provision|configure|all]` (default `all`).
It stays idempotent and stays under `set -euo pipefail`. Every helper must end
in an `if`, never on a `[ … ] && …` list (CLAUDE.md trap).

- **`provision`** has to be chroot-safe. That means no `systemctl start`,
  `restart`, `is-active` or `daemon-reload` that assumes a running systemd,
  and no `hostname -I`. `systemctl enable` is fine in a chroot. It:
  1. Installs the apt packages (add `git`, which the updater needs).
  2. Creates the `describer` user and the directories above.
  3. **Gets the code into `/opt/describer`.** If the script is running from
     another checkout, it clones that checkout's `origin` URL into
     `/opt/describer` at the same commit. If `/opt/describer` already exists,
     it leaves it alone. `DESCRIBER_REF` can pin a ref (the image build uses
     this).
  4. Builds the venv, installs Piper and the voice, and draws the cursor theme
     (moved as it is).
  5. Seeds `/etc/describer/config.yaml` from `config.example.yaml` if absent.
     In that seeded file it sets `sources.fallback: null`, with a small Python
     one-liner using the venv's PyYAML. That keeps the first-run story
     RDM-only.
  6. Creates an empty `describer.env` (600, `describer`) if absent.
  7. Installs the units, the polkit rules (Phase 3 adds more), the shutdown
     button, and `systemctl enable`s them.
  8. Sets `graphical.target` as the default.
- **`configure`** runs only on a real system with a terminal. It:
  - asks for the credentials (the current `ask` / `quote` code, unchanged)
    and writes them into `/etc/describer/describer.env`
  - runs `daemon-reload` and restarts the services
  - prints the board and `/admin` URLs
- **`all`** runs `provision` then `configure`.

### Migration from the old layout (Graham's Pi)

This runs inside `provision` when it finds `~SUDO_USER/describer` with a
`.config/systemd/user/describer.service` beside it:

1. Copy `~/describer/config.yaml` to `/etc/describer/config.yaml` if the
   target is absent. Otherwise leave both and print which one wins now.
   **Never overwrite `/etc/describer/config.yaml`.**
2. Copy `~/describer/voices/*` and `.piper/` into `/opt/describer` if
   missing, to save the download.
3. Leave `/etc/describer/describer.env` as it is, and chown it to `describer`.
4. As `SUDO_USER`, run `systemctl --user disable --now describer.service` and
   remove the user unit.
5. Don't delete `~/describer`. Print a line saying it can be deleted once the
   board is confirmed working.

### App changes

- `updater.py`:
  - The restart becomes `systemctl restart --no-block describer.service`
    (system scope). Keep the command in one constant so the tests can
    assert it.
  - `under_systemd()` stays as it is.
- Add a polkit rule, `deploy/polkit/50-describer.rules`, installed to
  `/etc/polkit-1/rules.d/`. It lets user `describer` run
  `org.freedesktop.systemd1.manage-units` only for unit `describer.service`
  and verb `restart`. Check that the polkit on current Pi OS takes JS rules
  (it does from 0.106, but confirm in the package version and note it).
- `credentials.py`: `DEPLOYED_ENV_FILE` is unchanged, and `rtt_allowed()`
  still keys off `/etc/describer`. Check its docstrings for "user unit"
  wording.
- Search the whole repo for `--user`, `%h`, `~/describer`, `enable-linger`
  and `$HOME/describer`, and fix every hit: code, README, CLAUDE.md,
  `config.example.yaml`, and the admin text.

### Tests

- `test_updater.py`: the restart command is the system-scope one.
- Add a `tests/test_install_sh.py` that runs `bash -n deploy/install.sh`.
  If `shellcheck` is on PATH, it also runs `shellcheck`, and skips it if not.
  It asserts the script never uses `systemctl --user`, and that `provision`
  never calls `hostname -I` or `systemctl restart`. A grep over the function
  body is enough.
- Existing tests all pass: `env -u RTT_TOKEN -u RDM_API_KEY .venv/bin/python
  -m pytest -q`, `ruff format`, `ruff check`.

### Docs in the same PR

- README "Pi installation": new paths, `sudo ./deploy/install.sh`, and
  `systemctl status describer` / `journalctl -u describer` without `--user`.
  A short "Moving from an older install" section.
- CLAUDE.md:
  - the Stack, Project layout and Running sections
  - the "Unit files are copied" trap (Phase 3 changes it again)
  - the `EnvironmentFile` trap: now there is only one file, so reword it
    rather than delete it, because the history still matters
- `config.example.yaml`: the comment on where the file lives.

**Stop. Gate G1, then G2.**

---

## Phase 2: first-run setup screen and `/setup` wizard

**Repo:** describer. **Branch:** `easy-install-2-setup`. One PR.

### When setup is needed

A new `describer/setup.py` works out one value, `setup_state(poller, store)`:

- `None` means no setup is needed.
- `{"reason": "no_key"}` means `RDM_API_KEY` is absent. Use the existing
  `credentials.status()` / `has_credentials`.
- `{"reason": "key_rejected"}` means the primary is RDM, the last RDM error
  was HTTP 401 or 403, and there is no good board since.
  - `ldbws.py` currently flattens this into `RailApiError("HTTP 401 for
    PAD")`. Give `RailApiError` an optional `status: int | None`, and have
    the source manager remember whether the last RDM failure was an auth
    failure.
  - A 401 must **not** stop polling. It is reported as before; this is only
    about what the screen says.

Only RDM counts. An image with no RTT token is normal and never needs setup.

Each state also carries:
- `url`: `http://<hostname>.local:8080/setup`
- `ip_url`: `http://<lan ip>:8080/setup`

Get the LAN IP with the UDP-connect trick (`socket.connect(("192.0.2.1",
80))` sends no packet). Get the hostname with `socket.gethostname()`. If
there's no IP, `ip_url` is `null`.

### Board

- `/api/state` and the SSE payload gain `setup` (the value above).
- `index.html` gets a full-screen `.setup` element outside the `.board`
  elements, so the grid-row trap doesn't apply. It's theme-neutral and styled
  in `base.css` only. Add `[hidden] { display: none; }` beside any `display`
  rule on it (CLAUDE.md trap).
- It shows:
  - a big heading: "Set up your departure board" for `no_key`, or "Your rail
    data key was not accepted" for `key_rejected`
  - the QR code
  - `describer.local:8080/setup` in large type
  - the IP address URL underneath
  - one plain line: "Scan with your phone's camera. Your phone must be on the
    same Wi-Fi."
- While it's showing, themes still load, but the boards are hidden. When
  `setup` becomes `null`, the page renders the boards with no reload.
- QR code: add **`segno`** to `requirements.txt` (pure Python, no deps). Serve
  `GET /api/setup/qr.svg`, which encodes `ip_url` when there is one (it works
  on every phone), otherwise `url`. Use colours from the page's CSS custom
  properties, not hard-coded ones. `Cache-Control: no-cache`, like the rest.

### `/setup` page

These are new static files, `setup.html`, `setup.css` and `setup.js`: plain
ES modules, no deps, mobile-first (375 px wide is the design width). The page
is served at `/setup` with `no-cache`, the same as `/admin`. It stays usable
after setup, and `/admin` gets a link to it.

1. **Station.** Extract the station combobox from `admin.js` (around line
   128–300: the `stations.json` fetch and the combobox) into
   `web/static/stationsearch.js`, and import it from both pages. `admin.js`
   must behave exactly as before, so check by hand in the browser pane. Also
   offer a departures/arrivals choice, and an optional second station ("Add
   another station"). The rest of the station options keep their defaults.
2. **Rail data key.** A numbered walkthrough, written in plain language:
   1. Create a Rail Data Marketplace account at raildata.org.uk.
   2. Find the product called *Live Arrival and Departure Boards* (the one
      whose name includes arrivals). **Don't pick the departures-only one.**
   3. Subscribe (it's free).
   4. Open the subscription and copy the *Consumer key*.
   5. Paste it here.

   Leave `<!-- screenshot: … -->` placeholders for Graham to fill in, with the
   image files under `web/static/setup/`. **Verify the product name and field
   names against `docs/design/` and the README before writing them**, and
   mark anything unconfirmed with `TODO(graham): confirm wording`.

   Then **Test key** calls `POST /api/setup/test-key {key, crs}`. It makes
   **one** LDBWS request with that key for that station, via the existing
   client, with the key passed in rather than read from the environment. It
   answers one of:
   - `ok`, with the station name and the next train
   - `rejected` (401/403)
   - `wrong_product`, if the response shows the key lacks the arrivals and
     departures operation. Work out from the client which status that is,
     and if it can't be told apart, fold it into `rejected` with wording that
     mentions both.
   - `unreachable`

   It never logs the key. RDM has no tight allowance, but it's still one
   call per click. Don't retry automatically.
3. **Sound.** Offer announcements on or off. If on, offer HDMI or headphone
   jack (`announcements.audio_device`), and a **Play a test** button that
   reuses `POST /api/announce/test`.
4. **Finish.** `POST /api/setup/complete {station(s), key, announce,
   audio_device}` does, in this order:
   - writes the key through `credentials.write`
   - updates the raw config through `ConfigStore.get()` / `set()`. Use the
     raw config, never `active()` (CLAUDE.md: mixing them up writes a profile
     into the base file).
   - calls `poller.sources.credentials_changed()`

   It answers with the new state. The page then says "Done: look at the TV",
   and links to `/admin` for everything else.

   Validation is the existing pydantic models; show their messages next to
   the field.

The key field is write-only, as in `/admin`: once saved it's never shown
again. On a re-visit the page says "A key is saved. Paste a new one only to
replace it."

### Tests (all offline)

- `setup_state` for: no key; key set with a good board; 401 and 403 from RDM;
  RDM failing with 500 (not setup); RTT missing (not setup).
- `/api/setup/test-key` with `httpx.MockTransport` for each outcome. Assert
  the key never appears in captured logs, at any level.
- `/api/setup/complete` writes the credential file (into `tmp_path` via
  `DESCRIBER_ENV_FILE`) and the config file, and leaves profiles untouched.
  It must not write a profile's stations into the base file: run it with a
  profile active, and check the file afterwards.
- `/api/setup/qr.svg` returns SVG and encodes the IP URL.
- conftest: no new upstream, so no new guard. Confirm this in the PR
  description.

### Verify by hand (browser pane, per CLAUDE.md)

- Start the dev server with `RDM_API_KEY` unset. The setup screen shows at
  1920×1080 and 1280×720 on every theme, and nothing is cut off. Use the
  truncation check on its text.
- `/setup` at the `mobile` preset: every step, the key test with a deliberately
  wrong key (one real RDM call, 401 expected), then the real key from `.env`.
- `/admin`: the station combobox works exactly as before.
- **Never** set `RTT_TOKEN` or force a source while doing this.

### Docs

README (user-facing: the setup screen and `/setup`), CLAUDE.md (Features: a
"First run" subsection; layout: the new files), `config.example.yaml`
(nothing new, unless a key was added).

**Stop. Gate G3.**

---

## Phase 3: `/admin` Shut down, and updates that apply `deploy/`

**Repo:** describer. **Branch:** `easy-install-3-updates`. One PR.

### Shut down and restart from `/admin`

- `POST /api/system/poweroff` and `POST /api/system/reboot`, each behind a
  confirm dialog in `/admin` → Status: "The screen will go blank. Wait for the
  green light to stop flashing before unplugging."
- The backend runs `systemctl poweroff` / `reboot`, and they are allowed by a
  polkit rule for user `describer` on `org.freedesktop.login1.power-off`,
  `power-off-multiple-sessions`, `reboot` and `reboot-multiple-sessions`.
- Off a Pi (no `/etc/describer`), the endpoints answer 409, "Only on the Pi",
  and do nothing. **A dev Mac must never power off from a test.** Make the
  command injectable, and assert in a test that the default is never called.

### Applying `deploy/` after an update

Today `/admin` says "re-run deploy/install.sh". That step must go.

- A new `deploy/apply-deploy.sh` is installed **root-owned** to
  `/usr/local/sbin/describer-apply-deploy`. The new
  `describer-apply-deploy.service` (`Type=oneshot`, `User=root`) runs it.
- The polkit rule lets `describer` **start** exactly that unit, and nothing
  else.
- **It must not trust the checkout**, which `describer` can write. It:
  1. Reads only the commit hash that `/opt/describer` is at
     (`git -C /opt/describer rev-parse HEAD`, with `-c
     safe.directory=/opt/describer`).
  2. Keeps its **own root-owned clone** at `/var/lib/describer-deploy`,
     cloned from the public GitHub URL (hard-coded in the script, not read
     from the checkout's remote), and fetches it.
  3. Refuses unless that commit is in the root clone and is an ancestor of
     (or equal to) the remote `main`.
  4. Runs `install.sh provision` **from the root clone at that commit**, so
     every root action comes from code that came straight from GitHub.
     `provision` must therefore be safe to run against an existing install
     without clobbering config, credentials or the checkout (it is idempotent
     already, so check that).
  5. Runs `daemon-reload`, restarts `kiosk` and `shutdown-button` if their
     units changed, and restarts `describer`.
  6. Logs every step to the journal.
- Updater: after a successful update where `deploy_changed` is true, start
  `describer-apply-deploy.service` (`--no-block`) **instead of** restarting
  the backend directly. The oneshot does the restart. In `/admin`, the
  "re-run deploy/install.sh" pills become "System files updated", or, if the
  unit failed, "System files not applied: see the log". Get that from
  `systemctl show -p Result describer-apply-deploy.service`.
- Write the threat model into the design note:
  - `/admin` is unauthenticated on the LAN. This design means a LAN attacker
    can only cause code from GitHub `main` to be installed, never their own.
  - Anyone with code execution as `describer` still can't reach root through
    this path.

### Tests

- `apply-deploy.sh`: `bash -n`, plus `shellcheck` if available.
- The updater starts the oneshot when `deploy/` changed and restarts directly
  when it didn't. Use a fake runner, as `test_updater.py` already does.
- Poweroff and reboot endpoints: 409 off-Pi. The command is never run in
  tests.

### Docs

- README: "Updating a Pi" loses the re-run step, and gains Shut down.
- CLAUDE.md:
  - rewrite the "Unit files are copied, not pulled" trap into the new rule
    (the apply-deploy service copies them, from its own clone)
  - add the polkit rules to the layout
  - the shutdown bullet in "Hardware and OS" mentions `/admin` too

**Stop. Gate G4.**

---

## Phase 4: the image (new repo `grahamlehr/describer-image`)

**Repo:** describer-image. Public, so the arm64 runners are free.

### Contents

```
describer-image/
  CLAUDE.md                  # what this repo is; points at this plan
  README.md                  # how to build and publish
  config                     # pi-gen config
  stage-describer/
    prerun.sh                # copy_previous
    00-describer/
      00-run-chroot.sh       # the whole stage
    EXPORT_IMAGE
  os_list.json               # the Imager repository, updated by the workflow
  .github/workflows/build.yml
```

### pi-gen

- Pin `pi-gen` to a tag for the current Pi OS release (arm64 branch). Use
  `STAGE_LIST="stage0 stage1 stage2 stage-describer"`. Remove the export from
  `stage2`, so the only image is ours.
- `config`:
  - `IMG_NAME=describer`, `TARGET_HOSTNAME=describer`, `LOCALE_DEFAULT=en_GB.UTF-8`
  - `KEYBOARD_KEYMAP=gb`, `TIMEZONE_DEFAULT=Europe/London`, `WPA_COUNTRY=GB`
  - `ENABLE_SSH=0`
  - Leave the first-user setup to Imager and first boot, however the current
    pi-gen spells that. **Check the pi-gen README for the tag in use**; don't
    assume the variable names.
- `00-run-chroot.sh`:
  ```bash
  git clone https://github.com/grahamlehr/describer.git /tmp/describer
  git -C /tmp/describer checkout "$DESCRIBER_REF"
  DESCRIBER_REF="$DESCRIBER_REF" /tmp/describer/deploy/install.sh provision
  rm -rf /tmp/describer
  ```
  `install.sh provision` clones into `/opt/describer` at the same ref.
  - Check that `/opt/describer`'s `origin` is the public URL and its branch
    tracks `main`, so the updater works from first boot.
  - Build `EXPORT_IMAGE` as xz.

### Workflow (`build.yml`)

- Triggers: `workflow_dispatch` with an input `describer_ref` (default
  `main`), and a monthly `schedule` so images carry recent OS fixes.
- `runs-on: ubuntu-24.04-arm`. Run pi-gen's own `build-docker.sh`, or
  `usimd/pi-gen-action` if it supports the pinned pi-gen and an arm64 host.
  Pick one and say why in the README.
- Outputs:
  - a Release tagged `image-YYYY.MM.DD` (suffix `-2` etc. on the same day),
    with the `.img.xz` and a `.sha256` attached, and the describer commit in
    the release notes
  - a commit to `os_list.json` with `url`, `extract_size`,
    `extract_sha256`, `image_download_size`, `release_date` and
    `init_format`
- **`init_format` is what makes Imager offer its settings (Wi-Fi, user,
  hostname) for a custom image.** Its correct value depends on the Pi OS
  release: `systemd` for the older `firstrun.sh` style, `cloudinit` (or
  `cloudinit-rpi` in newer Imager) for cloud-init based releases. Read the
  current rpi-imager source or the official `os_list` JSON for the matching
  Raspberry Pi OS Lite entry, and **copy the official entry's value**.
- The Imager JSON is served from
  `https://raw.githubusercontent.com/grahamlehr/describer-image/main/os_list.json`.

### Fallback if G5 fails

If Imager won't apply its settings to our image, the backup is a
`describer-wifi.txt` on the boot partition, which a first-boot oneshot turns
into a NetworkManager connection and then deletes. It's plain text the user
edits on the card in any file manager. Only build this if G5 fails.

**Stop. Gate G5.**

---

## Phase 5: the guide

**Repo:** describer. **Branch:** `easy-install-5-guide`.

`docs/INSTALL.md`, written for someone who has never used a terminal:

1. **What to buy:** a Pi 4B, the official power supply, a 16 GB or larger SD
   card, a micro-HDMI to HDMI cable, a monitor or TV, and optionally a case.
2. **Get a Rail Data Marketplace key:** the same steps as `/setup`, same
   screenshots.
3. **Write the card:**
   - install Raspberry Pi Imager
   - App Options → Content repository → the URL above
   - choose Describer
   - in the settings, set the Wi-Fi, keep the hostname `describer`, and set a
     username and password (write them down; you won't need them day to day)
4. **Start it:** plug in HDMI first, then power. Allow a few minutes on first
   boot.
5. **Set it up:** scan the QR code.
6. **Everyday use:** `/admin`, Shut down, updates.
7. **What the screen is telling you:** a table of each on-screen message (the
   setup screen, key rejected, data stale, a blank screen outside the
   schedule, "No services at this time") with what to do about each.

Mark screenshot placeholders for Graham. Link the guide from the top of the
README, as the "start here" route, ahead of the manual install.

Then write `docs/design/16-easy-install.md` in the house style: the decisions,
the reasons, what each gate measured, and the traps found. Add it to the
CLAUDE.md index, and delete this plan.

---

## Rules every phase follows

These restate CLAUDE.md, which wins if anything here disagrees.

- **Never call `data.rtt.io`.** Keep `RTT_TOKEN` unset, start dev servers
  only through `preview_start` (the `describer` entry), never force a source,
  and stop the server when done.
- `pytest` makes no network calls. New endpoints are tested with
  `httpx.MockTransport`.
- No key in logs, config or test output.
- Before each PR: `env -u RTT_TOKEN -u RDM_API_KEY .venv/bin/python -m pytest
  -q`, `.venv/bin/ruff format .` and `.venv/bin/ruff check .`.
- A feature lands with its docs: README, CLAUDE.md, `config.example.yaml`,
  and the design note at the end.
- One phase per PR. Stop at every gate. Don't merge your own PR.
