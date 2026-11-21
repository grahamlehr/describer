# Addendum 24 — a blank cursor under every name

## Decision

The blank cursor theme `install.sh` writes to
`/usr/local/share/describer-cursors/default` now answers to every CSS cursor
name and the usual X names and aliases (about 90), not just the nine arrow
and pointer names it had. `kiosk.service` sets
`XCURSOR_PATH=/usr/local/share/describer-cursors` alone; `/usr/share/icons`
is gone from it.

## Why

In November 2026 the Pi showed a mouse cursor over the board. Nothing in
`deploy/` or the board's `cursor: none` had changed since #27. The Pi's
`/api/status` showed one display restart from /admin (`kiosk.restarts: 1`,
reason `admin`, the button from note 20).

The Xcursor loader (libwayland-cursor and wlroots carry the same code) looks
for the theme in **every** directory on `XCURSOR_PATH`, loading the cursors
it finds in each, and follows the first `Inherits=` line it meets. Our
`index.theme` has none, so it went on to `/usr/share/icons/default`, whose
`index.theme` inherits Adwaita. Every name the blank theme did not provide
was therefore a real, visible cursor. The nine names covered cage's idle
pointer, but not, for instance, `progress` / `left_ptr_watch`, which
Chromium asks for while a page loads, or the shape names a newer
wlroots asks for through cursor-shape-v1.

Most likely cause on the Pi, not confirmed on the device: the restart from
/admin reloaded the page, Chromium asked for a loading cursor, and that name
fell through to Adwaita. A Pi OS update to cage, wlroots or Chromium that
changed which names are asked for would do the same.

## Trap

A blank theme is only blank for the names it carries, and only while nothing
else is on the path. Both halves are pinned in `tests/test_install_sh.py`.

## How checked

- `tests/test_install_sh.py` runs `install_cursor_theme` into a temporary
  directory and checks every CSS cursor name resolves to the transparent
  image, and that the unit's `XCURSOR_PATH` names only the blank theme's
  directory.
- An update applies it: `kiosk.service` changes, so `describer-apply-deploy`
  reinstalls the theme (through `install.sh provision`) and restarts the
  kiosk.
- Not verified on the device. To check on the Pi after the update: the board
  shows no cursor after boot and after **Restart display** in /admin, and
  `ls /usr/local/share/describer-cursors/default/cursors | wc -l` is about 90.
