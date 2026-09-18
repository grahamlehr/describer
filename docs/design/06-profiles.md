# Addendum 6 — Profiles: a different board at a different time of day

The board is read for different reasons at different hours. At half past
seven it answers "can I still make the 07:42", in the middle of the day
nobody is looking at it, and at six in the evening it is the other direction
from the other station. One `stations:` block and one theme cannot serve all
three, and editing `/admin` twice a day is not a feature.

A **profile** is a named window of the week carrying a sparse override of the
config. The one in force is resolved on every poller tick; everything
downstream reads the resolved config instead of the file's. **A config with no
profiles resolves to itself**, so this changes nothing for a board that does
not use it.

## Shape

```yaml
profiles:
  enabled: true
  entries:
    - name: Morning rush
      days: [mon, tue, wed, thu, fri]
      start: "06:30"          # start == end is all day; start after end wraps midnight
      end: "09:30"
      stations:                     # present = replaces the list wholesale
        - {crs: ABW, mode: departures, rows: 10, platforms: ["1"]}
        - {crs: LBG, mode: arrivals,  rows: 10}
      display: {theme: thameslink}  # a key left out keeps the base value
      announcements: {enabled: true, lead_time: 180}
    - name: Evening
      days: [mon, tue, wed, thu, fri]
      start: "16:30"
      end: "19:30"
      stations: [{crs: LBG, mode: departures, rows: 8}]
      display: {theme: splitflap}
```

- **Ordered, and the first match wins.** Overlaps and gaps are legal rather
  than rejected, because the alternative is a validation error standing
  between the user and a board. What falls through the list is the base
  `stations:` / `display:` / `announcements:` already in the file, which
  makes the config you have today the "every other hour" template without
  anyone having to write one.
- `start`/`end` wrap midnight exactly as `schedule:` does, and `days` names the
  weekdays it applies on. They are not `from`/`to` because `from` is a Python
  keyword, and the alias needed to keep the YAML key would have had to be
  threaded through every `model_dump` in the app to come back out again.

## What a profile may override, and what it may not

| Overridable | Not |
|---|---|
| `stations` (whole list) | `sources` |
| `display.theme`, `clock`, `show_calling_points` | `display.resolution` |
| `display.themes.<theme>` — options and the Addendum 5 palette | `schedule` |
| all of `announcements` bar the Piper paths | |

- **`stations` is replaced, never merged.** Merging two lists positionally
  would have to decide what a half-specified second station means, and the
  thing a profile wants is a *different* station, not a tweaked one.
- **`sources` is out.** Poll intervals and failover are plumbing, not
  presentation, and a profile that could set `poll_interval` or `primary` is a
  way to spend the RTT allowance on a schedule without noticing. Addendum 4
  stands.
- **`display.resolution` is out.** It is a hardware action with a retry loop
  behind it (`_apply_display_mode`), and no template needs the monitor to
  change mode at half past six.
- **Piper's paths are not overridable.** `voices_dir`, `piper_binary` and
  `cache_dir` describe the machine, not the hour.
- The override models are their own all-optional pydantic models. They cannot
  reuse `DisplayConfig` or `AnnouncementsConfig`: those carry defaults, and a
  default is indistinguishable from an override once it is parsed, so every
  unset field would silently overwrite the base.
- `null` inside an override means "not set here" and is dropped before the
  merge, so a profile cannot push a key *back* to null. The one place that
  bites is a palette role: a profile cannot return a colour the base config
  sets to the stylesheet's own. Nothing needs it, and the admin page does not
  offer per-profile colours at all.
- **A theme's options are typed loosely** (`themes: dict`), because a theme's
  options are its own. What proves them is the merge: `Config` validates every
  profile against itself at load and at Save, so a bad `phosphor` is refused at
  the button rather than at 06:30. That error arrives as a `ValueError` inside
  pydantic's `ctx`, which does not survive JSON — `/api/config` reports errors
  with `include_context=False`.

## Profiles choose content; `schedule:` still owns the power

Two time systems in one file is awkward, and the temptation is to fold the
display schedule in as an "off" profile. Don't. `schedule:` blanks the HDMI
output and stops the poller; a profile only decides what is on a screen that
is already awake. **The schedule wins**: while the display is off no profile
is active and nothing is fetched.

## `describer/profiles.py`

Pure — no I/O, no state:

- `in_window(on, off, now)`, factored out of `schedule.py`, which already
  gets the midnight wrap right. `window_for` and `is_display_on` are rewritten
  on top of it so there is one implementation of that arithmetic, not two.
- `active_profile(config, now) -> ProfileConfig | None`
- `resolve(config, now) -> Config` — deep-merges the override and returns a
  fully validated `Config`. Cached on the profile name and the base config's
  identity; it is asked for on every tick and the answer changes four times a
  day.

## `ConfigStore` grows a second accessor

This is the whole of the backend change, and getting it wrong is what will
hurt:

- `get()` — **raw**, exactly as today. `/api/config` GET and PUT and the admin
  page must see the file, not the resolution of it, or saving at 07:00 writes
  the morning profile into the base config.
- `active(now=None)` — resolved. The poller, `state()`, the announcer and
  `/api/status` all read this.
- `force_profile(name | None)` — memory only, never written to YAML, mirroring
  `force_source`. Seeing the evening board at eleven in the morning is the
  only way to check it without waiting.

Every `self._store.get()` in `poller.py` becomes `active()`. `_tick` must take
**one** snapshot and hand that same object to `_notify_listeners`: the
announcer indexes `config.stations[index]` against the boards it was given, so
a resolution taken twice in one tick can pair a board with another profile's
station.

## Switching profiles, in the poller

Detected in `_tick`, beside the existing `is_display_on` check. On a change:

- log at INFO, and `_publish(self.state())` straight away, so the browser
  re-themes and re-lays-out without waiting for the next poll;
- clear `_next_due` and `_failures` — a station that has just appeared has no
  board and must be fetched now;
- **prune `_boards` of every slot not in the new station set.** `boards()`
  hands back a cached board without looking at its age; only a *failed* fetch
  ever marks one stale. Left alone, an evening profile returning to a morning
  station would render a three-hour-old board as live, with no warning on it
  at all. Slot keys already carry the CRS and mode, so nothing collides — the
  stale cache is the only hazard.

The forced re-poll costs one board call per station per switch. Four switches
a day is eight calls, immaterial against RTT's hourly allowance, and the
`min_poll_interval` floor applies as normal afterwards. Do not special-case it.

## The announcer forgets on age, not on absence

`_prune` used to keep only the identities present on the current boards, so a
station leaving the board dropped everything announced for it, and restoring
that station later in the day announced every train on it again. `_announced`
now carries a last-seen timestamp and is pruned against `FORGET_AFTER` (thirty
minutes) instead. Profiles are what would have made that happen daily rather
than never, which is why it was fixed alongside them.

## Surfaces

- `/api/status` gains `active_profile`, `forced_profile`, `next_profile` and
  `profile_changes_at`; the admin Status block shows them, and a **Force
  profile** select sits next to Force source.
- `/api/state` carries `active_profile` so a theme could name it. None do.
- **The board needs no change.** `render()` re-applies the theme from every
  state frame and `ensureBoards(state.boards.length)` already copes with two
  boards becoming one; `detach()` clears the outgoing theme's timers. What
  wants checking by hand is a live switch *into* `nse` or `led-matrix`, where
  the canvases size themselves from a `--chars` that arrives with the
  stylesheet a moment after the switch (Addendum 3).

## /admin, and the trap in it

- `readForm` sweeps every `[name]` field and `set()`s it by dot path.
  **Profile fields must stay out of that sweep**, or a field named
  `display.theme` inside a profile card writes the base theme. Follow the
  pattern `stations` already uses: read the editor with its own
  `readProfiles()` and assign `draft.profiles` after the sweep.
- `renderStations` / `readStations` are wired to `#stations`. Generalise both
  to take a container element and reuse them in the profile editor rather than
  writing a second station card.
- **The theme options and the colour picker are singletons keyed by element
  id** — `#theme-options`, `loadDefaults`, `syncColours`, `renderPreview` all
  assume one theme is being edited. Do not render one per profile. The
  Profiles tab is a *list* plus **one editor for the selected profile**, which
  reuses that machinery unchanged against the selected profile's draft, is
  less code, and is a better editor than eight cards competing for the width.
- A 24-hour coverage ribbon per weekday earns its hour: first-match-wins is
  only hard to reason about until you can see the gaps and the overlaps. It
  samples every ten minutes and **repeats the window arithmetic in JavaScript**,
  because the answer has to be drawn from the unsaved draft. `coversMinute` and
  `in_window` say the same thing twice; change both.
- The editor writes a profile's theme *options* but not its colours. A palette
  belongs to a theme rather than to an hour, so every period showing thameslink
  shows the same thameslink; the model still carries a per-profile palette
  written by hand.
- `THEME_OPTIONS` in `admin.js` describes the same options the Display tab
  spells out in HTML. A new theme option belongs in both.
- The `novalidate` rule holds. Anything `required` added to this panel must be
  reachable by `revealTabFor`.

## Tests

- `test_profiles.py`: window matching including a wrap past midnight, weekday
  filtering, first-match precedence, no match resolving to the base config,
  a sparse merge keeping base values, `stations` replaced rather than merged.
- `test_poller.py`: a switch prunes the boards it must not reuse, re-polls,
  and publishes state.
- `test_announce_scheduler.py`: a station removed and restored does not
  announce its trains twice.
- `test_config.py`: a file with no `profiles:` key loads unchanged; duplicate
  names and malformed times are rejected.
- `test_app.py`: `/api/config` returns the raw config while `/api/state`
  reflects the active profile.

## Out of scope

Per-profile `sources`, dates and one-off overrides (a bank holiday, an
engineering weekend), profiles triggered by anything but the clock, and
transitions of any kind — a profile change is a cut, not a fade.
