# Installing by hand and looking after a Pi

The quick route is the ready-made image in the [README](../README.md). This page is for
installing on a Pi OS Lite you already have, and for the commands behind the day-to-day jobs.

## Requirements

- Python 3.11+
- A Rail Data Marketplace subscription to the **Live Arrival and Departure
  Boards (LDBWS)** product, which gives you an API key. The departures-only
  product will not do: it has no arrivals operation at all
- Optional but recommended: a free [Realtime Trains](https://api-portal.rtt.io)
  API token for the fallback source
- For announcements: [Piper](https://github.com/rhasspy/piper) and a British
  English voice
- On the Pi: Raspberry Pi OS Lite 64-bit, plus `cage` and `chromium`
  (installed by `deploy/install.sh`). Current Pi OS has no `chromium-browser`
  package; the binary is `/usr/bin/chromium`

## Pi installation

```bash
git clone <this repo> && cd describer
sudo ./deploy/install.sh
```

The install is root-run and idempotent, and does everything: system packages,
a `describer` system user, the checkout at `/opt/describer`, the venv, Piper
and a voice, config and credential files under `/etc/describer`, and three
systemd services. It runs in two parts if you want them separately
(`sudo ./deploy/install.sh provision` then `configure`); `all` (the default)
runs both.

- `describer.service` — a **system** service, `User=describer`, running the
  FastAPI backend on port 8080 from `/opt/describer`
- `kiosk.service` — a **system** service on tty1 running `cage` and Chromium
  in `--kiosk` against localhost, also as `describer`. It has to be a system
  service: cage needs a logind seat for the display and input devices, and a
  lingering user session never gets one. The login prompt on tty1 is
  switched off to make room for it; press Ctrl+Alt+F2 for a console login.
- `shutdown-button.service` — a **system** service watching a button for the
  clean-shutdown gesture (below).

### Moving from an older install

An install from before this layout kept the checkout at `~/describer` and ran
`describer.service` as a user unit for whoever owned it. Running
`sudo ./deploy/install.sh` (as that same user, via `sudo`) detects that old
layout automatically and migrates it: it copies `~/describer/config.yaml` to
`/etc/describer/config.yaml` (only if the new path is empty — it never
overwrites one already there), copies `voices/` and `.piper/` across to save
re-downloading them, chowns the existing `/etc/describer/describer.env` to
the new `describer` user, and disables the old per-user unit. It does not
delete `~/describer`; delete it yourself once the board is confirmed working
under the new layout.

### Shutdown button

Cutting mains power on a running Pi risks the SD card, so a momentary
normally-open button gives a safe "off" with no keyboard or network. Wire one
leg to **GPIO21 (pin 40)** and the other to **GND (pin 39)**; the internal
pull-up means no resistor. **Six presses within 10 seconds** runs
`systemctl poweroff`. Wait for the green ACT LED to stop flashing before
pulling the power. There is no abort once the sixth press lands, and testing
it means cycling power to bring the Pi back.

**No button? Use the page.** **/admin → Status → Power** has **Shut down** and
**Restart** (and **Restart display**, which only restarts the browser if the
board is frozen or showing an error; it also does that by itself), each behind a confirmation. It does the same clean poweroff (or
reboot), and needs no wiring. Anyone on your network who can open `/admin` can
press them, the same trust as the rest of that page. On a development machine
they answer "Only on the Pi" and do nothing. The button on GPIO21 is
unchanged and still works when the network does not.

The watcher is `deploy/shutdown_button.py`, installed to
`/usr/local/bin/shutdown-button` and run by the system Python with the apt
`python3-gpiozero`/`python3-lgpio` (not the venv). Only one process can own
GPIO21, so any other gesture on that button belongs in the same script.

```bash
sudo journalctl -u shutdown-button -n 5   # "watching GPIO21 for 6 presses within 10s"
```

`sudo deploy/install.sh configure` asks for both sets of credentials and
writes them to `/etc/describer/describer.env` (mode 600, owned by
`describer`). On a re-run, pressing Enter at a prompt keeps the credential
already there. To change them later, edit that file and:

```bash
sudo systemctl restart describer kiosk
sudo systemctl status describer kiosk shutdown-button
sudo journalctl -u describer -f
sudo journalctl -u kiosk -f
```

`describer.service` reads only `/etc/describer/describer.env` — a system
install has no checkout `.env` for it to read at all, which removes a whole
class of the "blanked key" trap a dev-machine `.env` could once cause. If
credentials appear to be missing, `/api/status` reports `credentials`, and
this settles it:

```bash
sudo systemctl show describer -p Environment | tr ' ' '\n' | grep -c '^RDM_API_KEY=.\+'
```

Keys can also be set or cleared from **/admin → Data sources → Credentials**,
which writes them to `/etc/describer/describer.env` and puts them to work
without a restart. It is write-only: a saved key is never shown again.

The unit files are *copied* into `/etc/systemd/system/`, so a bare `git pull`
does not update them. **Updating from /admin does** (below); only a hand-run
`git pull` leaves them behind, and `sudo /opt/describer/deploy/install.sh
provision` catches them up.

### Updating a Pi

The board checks GitHub every six hours (`updates:` in the config). When a
newer release is waiting, **/admin → Status → Updates** lists it, and **Update
and restart** installs it: it fast-forwards the checkout, installs
requirements if they changed, checks the new code starts with your config,
and restarts the backend (system scope: `systemctl restart describer.service`).
The board reloads itself when it comes back. If the new code will not start,
the checkout is put back and nothing restarts.

It will not update a checkout with local changes to tracked files, one on
another branch, or one with commits of its own.

**When a release changes `deploy/`** (the unit files, the polkit rules, the
shutdown-button watcher), there is nothing to re-run. After the update the
backend starts `describer-apply-deploy.service` instead of restarting itself,
and that root oneshot installs the new files and restarts what changed,
including the backend. **/admin → Status → Updates** then reads **System files
updated**, or **System files not applied: see the log** if it failed:

```bash
sudo journalctl -u describer-apply-deploy
```

That service does not take your checkout's word for what to install, since
the checkout belongs to the unprivileged `describer` user. It reads only the
commit the checkout is at, and installs from its own clone of the public
GitHub repository at `/var/lib/describer-deploy`, and only if that commit is on
GitHub's `main`. So a page anyone on your network can open can make the Pi
install what is on GitHub, never anything else. The reasoning is in
[`design/13-credentials-and-updates.md`](design/13-credentials-and-updates.md).

A Pi that was installed before this existed does not have that service yet,
so the update that brings it in restarts the backend directly and says **System
files were not applied**. Run `sudo /opt/describer/deploy/install.sh provision`
once, and every update after that applies its own.

By hand:

```bash
cd /opt/describer && sudo -u describer git pull
sudo systemctl restart describer kiosk
```

Static files are served with `Cache-Control: no-cache`, so Chromium checks
every file with the server and picks up new themes on the reload. A Pi that
was last updated before that header existed may still be holding an old file
its browser decided was fresh, which shows up as a new theme drawn with the
previous release's stylesheet. Clearing the browser cache once fixes it for
good:

```bash
sudo systemctl stop kiosk
sudo -u describer rm -rf /var/lib/describer/.cache/chromium
sudo systemctl start kiosk
```
