# Addendum 19 — kiosk watchdog

## Decision

The board page posts a heartbeat to `POST /api/kiosk/heartbeat` at start-up
and every 15 s. `describer/kiosk.py` (`KioskWatchdog`) checks every 15 s; if no
heartbeat from the Pi itself has arrived for 90 s it runs `systemctl restart
--no-block kiosk.service`. `/admin → Status → Power` has a **Restart display**
button for the same restart by hand, and `/api/status` carries a `kiosk`
section (last heartbeat age, restarts, last reason, last error).

## Why

`kiosk.service` has `Restart=always`, which only covers Chromium exiting (then
cage exits and systemd restarts it). A crashed renderer (the sad tab), a page
stuck in a script or layout loop, or a Chromium error page leaves the process
alive, so nothing recovered until someone rebooted.

- **Not SSE presence.** A hung renderer keeps its socket open, so a connected
  stream proves nothing. A `setInterval` callback only runs if the main thread
  does.
- **Not a systemd `WatchdogSec`.** That watches a process that can notify
  systemd; Chromium cannot, and the thing to prove is the page, not the process.

## Numbers

- Heartbeat 15 s (`board.js`, `HEARTBEAT_SECONDS`); check every 15 s;
  stale after 90 s (six missed beats, so a slow page load or a backend restart
  from an update does not trip it); after 3 restarts with no heartbeat between,
  one attempt per 600 s (a screen unplugged or a broken Chromium must not
  restart-loop).
- The staleness clock starts from the latest of: last heartbeat, backend start,
  last restart, and the last time the kiosk was seen not active.

## Guards and traps

- **Loopback only.** The kiosk loads `http://localhost:8080/`; a phone or Mac
  opening the board must not count as the kiosk being alive.
- **`systemctl is-active kiosk.service` must say `active`** before a restart. A
  kiosk stopped or disabled by hand (or mid-restart) stays off. `is-active`
  exits non-zero for "no", so it uses `system.query_command`, which returns
  stdout whatever the exit code.
- **Only on a Pi under systemd** (`system.on_pi()` and `updater.under_systemd()`).
- Commands go through `system.run_command` / `system.query_command`, looked up
  at call time; `conftest.py` replaces both with tripwires.
- **The polkit grant (`kiosk.service` restart) reaches the Pi only through an
  update that touches `deploy/`**, applied by `describer-apply-deploy`. Until
  then the restart is refused and `kiosk.error` says so.
- Only `board.js` sends the heartbeat, not `/admin` or `/setup`.

## How checked

Unit tests with a fake clock and fake runner (`tests/test_kiosk.py`), and the
polkit rule pinned in `tests/test_apply_deploy.py`. Not verified on a device:
kill Chromium's renderer on the Pi (`pkill -f type=renderer`) and expect the
board back within about two minutes.
