# Traps and values that live in two places

_Moved from the old CLAUDE.md ("Traps" and "Values that live in two places"). Each has broken the board at least once._

Each of these has broken the board at least once. The design note named has
the full story.

- **`hidden` is beaten by an author `display`.** Any rule that sets `display`
  on an element `board.js` hides needs `[hidden] { display: none; }` beside
  it. (`03`)
- **Every child of `.board` names its own `grid-row`.** `.stale` is
  `display: none` most of the day; with auto-placement `.rows` fell into an
  `auto` track, its size container resolved to zero height and the board
  rendered blank. (`03`)
- **A row must be in the tree before its cells are painted.** A detached
  element has no computed style, so `--chars` reads empty. `setText`
  short-circuits on `dataset.rendered`, so a cell painted wrong stays wrong.
  (`03`)
- **Size from the configured row count, not the services in hand**, so the
  type holds still as trains drop off. (`03`)
- **A theme that writes its own `--slot` must add `--reason-share`** (and
  `--detail-share` / `--weather-share` if it opts in to those). thameslink
  deliberately does not. (`03`, `07`, `15`)
- **The nse watchdog must not be re-armed while one is pending**, or it never
  fires and the board freezes part-swept. (`03`)
- **thameslink: measure the stops against `.calling-points`, never the
  track**, and observe the block with a `ResizeObserver`. (`03`)
- **`glide` reads the transform before setting `transition: none`**, or the
  route leaps to the foot mid-scroll. (`09`)
- **Profile fields stay out of `readForm`'s sweep**; so do the station search
  box and the credential fields (no `name`, events stopped at the field).
  (`06`, `10`, `13`)
- **The poller takes one `active()` snapshot per tick** and hands that same
  object on; the announcer indexes stations against the boards it was given.
  On a profile switch, prune `_boards` of slots not in the new set, or a
  three-hour-old board renders as live. (`06`)
- **The setup screen owns its colours.** The themes do not share a palette
  (nse's body is a pale casing under a dark panel, and several have no `--bg`
  or `--fg`), so `.setup` takes `--bg`/`--fg` where a theme has them and black
  and white where not, and the QR code is always black on white. Sit it above
  crt's scanlines (`z-index: 6` against 5), or they draw lines across the code.
  Check any change to it on every theme at both sizes. It is a mask, so
  it needs no colour of its own in the SVG.
- **Setup writes through `get()`, and never overwrites a saved key with a
  blank.** `/api/setup/complete` treats a blank `key` as "keep the saved one";
  a test key is not installed until Finish. Neither `/api/setup/*` route may
  log, echo or return a key, in any outcome.
- **An empty assignment still assigns.** A bare `RDM_API_KEY=` in the
  checkout's `.env` once shadowed the deployed key on a per-user install and
  spent a day's RTT allowance — `describer.service` read the checkout `.env`
  first, and systemd applies environment files in order with the last
  assignment winning, so a blank line there beat a real key. The system unit
  now has only one `EnvironmentFile`, `/etc/describer/describer.env`, which
  removes the whole class of trap rather than just reordering it; the
  history is why `credentials.py` still clears a key by deleting its line
  instead of ever writing it empty. (`01`, `13`)
- **`install.sh` runs under `set -e`**: a helper must not end on a
  `[ … ] && …` list, or a false test ends the install. Use an `if`.
- **`deploy/` is applied by `describer-apply-deploy`, from its own clone,
  never from the checkout.** The checkout is `describer`'s to write and
  `/admin` has no login, so the root oneshot
  (`/usr/local/sbin/describer-apply-deploy`, from `deploy/apply-deploy.sh`)
  takes one thing from it, the commit id, and installs only if that commit is
  on GitHub `main`, running `install.sh provision` from its own root-owned
  clone at `/var/lib/describer-deploy`. Keep it that way: `provision` reads
  every file it installs from `$SCRIPT_DIR`, never `$TARGET_DIR`, and runs
  anything inside `/opt/describer` (venv, pip, tar, mkdir) through
  `as_describer`. The URL is hard-coded in the script, and the polkit rule lets
  `describer` start only that unit. A new unit file that is not copied by
  `install_units` is not applied by an update. (`13`)
- **The kiosk must not depend on the default target, and tty1's getty is
  switched off by a condition, never masked.** Raspberry Pi OS ends its first-boot user setup with
  `cancel-rename`, which (with no lightdm installed) runs `raspi-config nonint
  do_boot_behaviour B1`: the default goes back to `multi-user.target` and
  `getty@tty1` is enabled and started. On a hand install the user exists before
  `install.sh` runs, so nothing undid it; on a flashed image it runs *after*,
  and a kiosk wanted by `graphical.target` never started while the backend ran
  fine. So `kiosk.service` is `WantedBy=multi-user.target`. Masking
  `getty@tty1.service` to keep it off the board was the next mistake:
  `cancel-rename` ends with `systemctl enable getty@tty1` and `systemctl start
  getty@tty1`, both fail on a masked unit, so the script exits non-zero.
  cloud-init's user step then fails (two cloud-init units show as failed at
  boot) and never masks `userconfig.service`, whose `sh -e` script hits the
  same failure and is restarted by `Restart=on-failure`: the Pi asks for a
  keyboard layout, username and password for ever. A `Conflicts=` on the kiosk
  is no better, because that `start` would stop the kiosk. So `install_units`
  installs `deploy/getty-tty1-kiosk.conf` as a drop-in whose condition makes
  the getty a successful no-op while the kiosk is enabled, and the kiosk has no
  `Conflicts=`. A file check in the image build cannot see any of this: it
  happens on first boot.
- **`Restart=always` only covers Chromium exiting.** A sad tab, a hung page or
  an error page leaves the process alive; recovery needs the board's heartbeat
  and `KioskWatchdog`. A stream being open proves nothing, and only a loopback
  heartbeat counts. The restart needs the polkit grant, which reaches a Pi only
  with an update that touches `deploy/`. (`20`)
- **The blank cursor theme must cover every cursor name, and `XCURSOR_PATH`
  must hold nothing else.** The Xcursor loader scans every directory on the
  path for the theme and follows the first `Inherits=` it finds, so with
  `/usr/share/icons` on the path any name the blank theme lacked came from
  Adwaita and a real cursor sat on the board. (`24`)
- **An SSE stream never ends on its own.** Before a restart,
  `Poller.close_streams()` must run or shutdown waits for systemd to kill it.
  (`13`)
- **Every child of `.boards` is a board.** `ensureBoards` counts
  `boardsEl.children`, so a theme that adds its own element there has it
  removed or counted as a station. Put it after `.boards` as a sibling
  (victorian's clock) and remove it in `detach`. (`21`)
- **One clock a screen**, unless `display.clock_both`. On a split screen
  `board.js` otherwise hides every `.clock` but the last board's. A theme that draws its own from `.clock` must keep
  the hidden one's room if its halves are to stay level (thameslink), and
  must not read "this clock is hidden" as "the clock is switched off". (`21`)
- **`requestAnimationFrame` does not run in a hidden browser pane** and
  `setInterval` is throttled in a background tab: splitflap freezes
  mid-alphabet and page turns stop. Keep the pane displayed when checking
  animation. (`03`)

## Fonts and figures

- **Never use `tabular-nums` with Besley** (victorian). Its tabular zero does
  not follow the weight axis, so every 0 comes out at regular weight. And
  `base.css` sets `tabular-nums` on `.clock` itself, so changing it on `body`
  does not change the clock. Josefin Sans (deco) has no tabular figures at
  all; deco sizes `board.js`'s `.clock-char` cells instead (`/19`).
  victorian no longer shows the digits at all (`/21`).
- **`scrollHeight` over-reads a line of Besley.** Its ink overhangs the line
  box, so two lines measured 168px against a `clientHeight` of 150. Count
  lines from `clientHeight` (`/21`).
- **Do not re-insert a node that is already in place.** `renderRows` used to
  `append` every row on every frame; the browser repainted the whole list
  each time, which a heavy theme on the Pi could not afford (`/19`).

## Values that live in two places

Change every home, or they drift silently.

| Value | Homes |
|-------|-------|
| nse / led-matrix character pitch | `PITCH_EM` (0.1) in the JS; `0.6em` per character in the CSS |
| splitflap tile pitch | `TILE_EM` / `GAP_EM` in `splitflap.js`; `0.86em` / `0.08em` / `1.32em` in `splitflap.css` |
| `--calling-share` (0.9) | `base.css`; set per board in `board.js` |
| profile time windows | `in_window` in `profiles.py`; `coversMinute` in `admin.js` |
| theme options | the Display tab's HTML; `THEME_OPTIONS` in `admin.js` |
| a stop's time rules | `stopTime` in `board.js`; its copy in `thameslink.js` |
| announcement wording | `phrasing.py` (Piper's text); `PHRASES` in `catalogue.py` and `build_plan` in `clips.py` (recorded clips). `test_clips.py` fails when they part |
| the theme names | `ThemeName` in `config.py`; the radios in `admin.html`; the list in `admin.js` |
| the heartbeat interval (15 s) and the stale limit (90 s) | `setInterval(heartbeat, 15000)` in `board.js`; `HEARTBEAT_SECONDS` and `STALE` in `kiosk.py` |
| the backend's port (8080) | `PORT` in `setup.py` (the QR code's address); `--port` in the units, the kiosk URL and `.claude/launch.json` |
