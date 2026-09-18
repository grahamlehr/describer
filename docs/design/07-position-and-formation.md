# Addendum 7 — Where the train is now, and how it is made up

Adds one line under the top service, in `modern` and `thameslink` only: where
it is, worked out from the stops it has already left, and its formation, one
box per coach filled to how busy the feed says it is. Every other theme is
untouched — the line's flex share is nought unless the active theme opts in,
and a theme that has not never gets asked for either.

## What the step 0 capture found, against the plan's five assumptions

One `GetArrDepBoardWithDetails` call at LBG
(`tests/fixtures/lbg_arrdep_formation.json`) confirmed three of the plan's
five assumptions and corrected two:

1. Held — a departure carries `previousCallingPoints` alongside
   `subsequentCallingPoints`.
2. Held — a previous calling point's `at` is the actual departure there, and
   is `"On time"` as well as `"HH:MM"`; `"No report"` also appears, meaning
   the train has not reached that stop yet.
3. **Half held.** `coaches[]` does carry `coachClass`,
   `loading`/`loadingSpecified`, `number` and `toilet` — but `toilet` is a
   nested object (`{status, Value}`, `Value` one of `"None"` /
   `"Standard"` / `"Accessible"`), not a flat value, and **`formation.avgLoading`
   does not exist anywhere in the response.** `Formation.average_loading` is
   computed instead: the mean of the coaches with `loadingSpecified: true`,
   rounded to the nearest whole number, `None` when none of them are.
4. **Unconfirmed, not failed.** No service in the capture was at-platform, so
   `eta`/`etd`/`at` never read anything but a time, `"On time"` or
   `"No report"`. There is no "At platform" wording; `positionText`'s
   `approaching` case ("next stop here") already covers that case honestly.
5. **Corrected.** The plan assumed coaches arrive front-first. The capture
   showed the service itself carries `isReverseFormation` (bool), which the
   parser reads instead of guessing: `Coach` order reverses when it is set.
   Every service in the capture had it `false`, so `true` is unverified
   against real data, but the logic no longer depends on an assumption about
   which end is the front.

The capture also surfaced two things the plan had not anticipated: every
calling point, previous and subsequent alike, carries an explicit `isCancelled`
boolean, more reliable than the `et`-text heuristic the existing parser used —
`_calling_points`'s cancellation check now ORs the two, so it reads correctly
on real data without disturbing the old hand-written fixtures that only ever
set `et: "Cancelled"`. And a calling point can carry its own `formation`
(a forecast for that point in the journey); unused here, since only the
service's own formation feeds `Formation`.

## Data model (`rail/models.py`)

`Position` (`state`: `"not_started"` / `"between"` / `"approaching"`, `last`,
`last_time`, `next`, `stops_away`), `Coach` (`number`, `first_class`,
`accessible_toilet`, `loading`), `Formation` (`coaches`, `average_loading`).
`Service.position` and `Service.formation`, both `None` by default.
`CallingPoint.actual_time`, `None` until the stop is reported left.

## LDBWS parser (`rail/ldbws.py`)

- `_actual_time(point)` normalises `at` the way any Darwin estimate is:
  `"On time"` becomes the point's own `st`, `"Delayed"` / `"No report"` /
  empty become `None`, anything else passes through. Shared by
  `_calling_points`, which now sets `actual_time` on every point, and by
  `_position`.
- `_position(raw)` returns `None` when the service is cancelled or has no
  `previousCallingPoints` at all (it starts here). Otherwise it walks the
  non-cancelled previous calling points and finds the last one with a known
  `actual_time`: none found is `not_started` (`next` is the origin); all
  found is `approaching`; some found is `between`, with `stops_away` counting
  what is left. It reads `previousCallingPoints` straight off the raw service
  dict regardless of the board's mode, exactly as the plan specified — a
  departure gets a second read of the same list an arrival already parses.
- `_formation(raw)` returns `None` with no `formation` or no coaches.
  `first_class` is `"first" in coachClass.lower()` — only `"Standard"` was
  ever observed, so this is unverified against a real first-class coach.
  `accessible_toilet` is `toilet.Value == "Accessible"`. Coaches reverse when
  `isReverseFormation` is set. `Service.length` is backfilled from
  `len(coaches)` when the feed's own `length` is falsy.

RTT sets neither field; `test_rtt.py` asserts `position is None` and
`formation is None` on every RTT fixture, and that `length` is unaffected.
Revisiting this — a fresh read of RTT's per-stop `realtimeActual` for the top
row only, if the allowance has room — is future work, not done here.

## Config

`display.show_position` and `display.show_formation`, both default `true`, in
`DisplayConfig` and `DisplayOverride` beside `show_calling_points` — same
mechanism, same profile-override behaviour, each field independent of the
other. No new theme options and no new palette roles: the loading colours are
mixed from roles the themes already have.

## Frontend

### DOM and sizing

`.service-detail` (holding `.service-position` and `.service-formation`)
lives in the board template beside `.calling-points` and `.service-reason`.
`--detail-share` joins `--calling-share` / `--reason-share` in `base.css`'s
default `--slot`, at `0` unless the active theme opts in and there is
something to show. This is inert for every theme that defines its own
`--slot` without the term (`1990s`, `nse`, `led-matrix`, `thameslink`) and a
literal `+0` for the two that use the shared default (`crt`, `splitflap`), so
none of the five changed at all — verified by measuring the computed
`--slot` value and row height for each, not by eye.

### `board.js`

- `placeAfter(rowsEl, el, ...candidates)` replaced the two bespoke
  "anchor after the last visible thing" blocks that used to live in
  `renderCallingPoints` and `renderReason`; both now call it, and
  `renderDetail` does too. The order under the top service is
  row → detail → stops → reason.
- `renderDetail(boardEl, services, board, display)` checks
  `theme?.serviceDetail` first: every other theme gets `wrap.hidden = true`
  and `--detail-share: 0` unconditionally, before position or formation is
  even computed. When the theme opts in, the line hides when the board is
  stale, when both toggles are off, or when there is nothing either would
  draw. `show_position` / `show_formation` missing from a frame (true before
  Addendum 7's config fields reach `/api/state`, which they now always do)
  reads as on, so the toggles are additive rather than a prerequisite for the
  feature working at all.
- `positionText(service, mode)` is a theme hook of the same name, `null`
  accepting the default wording: `not_started` → "Not yet left X";
  `between` → "Left X 14:21 · 3 stops away" ("1 stop away"); `approaching` →
  "Left X 14:21 · next stop here".
- `renderFormation(el, formation, length)` is a full-override theme hook,
  like `renderCallingPoints`. The default builds one `.coach` span per coach
  (or, with no formation, `length` plain ones), each carrying `data-load`
  (`quiet` / `moderate` / `busy` / `unknown`, from `QUIET_BELOW = 35` and
  `BUSY_FROM = 70`), `--load` (0–1, for the fill), `data-first` and
  `data-toilet="accessible"` where they apply.
- `renderCallingPoints`'s theme hook gained a third argument, the service's
  full `calling_points` list; every theme but `thameslink` ignores it. The
  module contract table above now carries all of this.

### `modern`

*Superseded by Addendum 8: `modern` now draws the same car strip as
`thameslink`, on a line of its own. The share and colour mixing below still
hold.*

The 0.9 slot share decided up front, rather than folding the line into the
calling-points pages. Coaches are boxes outlined in `--muted`, filled from
`--on-time` / `--late` / `--cancelled` mixed with `--bg`; unknown is an empty
outline. First class gets a small "1" floated above the box rather than
overlaid on it, so the fill never has to compete with it for the same pixels
— stacking a mark inside the box would have needed a real child element with
its own `z-index`, since a pseudo-element painted after `::after` still sits
under it in paint order.

### `thameslink`

The line sits between the featured train and its route, `flex: 0 0 auto` and
deliberately outside `--slot` — the same reasoning as `--reason-share`: the
route holds the slack and simply turns one more page. Coaches fill `--fg`,
busy ones `--late` (this board has no green); a thin `color-mix()` outline
was added so an all-unknown formation still reads as coaches rather than
empty space, which the plan did not specify — judged by eye, in the manner of
Addendum 3's other unspecified constants.

**Arrivals route marker.** `renderCallingPoints(list, points, rawPoints)`
reads `rawPoints[i].actual_time` to find the first stop the train has not yet
left. Everything before that index gets `.tl-passed`, which dims the name and
the line together in one `opacity` rule, since both are painted by the one
element and its colour-inheriting pseudo-elements. `.tl-final` — the filled
dot — moves to that first-unreached stop instead of the last one, or stays on
the last stop once every one has been left ("arriving next"). A departures
route is unaffected: `rawPoints` is only consulted in arrivals mode.

## Decisions settled during the build

- The strip draws the front on the left, for both themes; no per-station
  `formation_front` setting.
- `QUIET_BELOW = 35` and `BUSY_FROM = 70` stand as named constants in
  `board.js`, unrevised — the LBG capture's loading figures are a 0–100
  scale, as assumed, giving no reason to think Darwin uses anything else.
- `Formation.average_loading` is computed by the parser rather than dropped,
  since `formation.avgLoading` turned out not to exist; `isReverseFormation`
  is applied rather than ignored. Both are corrections to the plan, not
  choices it offered.

## Tests

`test_ldbws.py` (every position state including cancellation-skipping and a
hand-built `not_started` case, formation parsing, `isReverseFormation`,
length backfill — against the real LBG fixture plus small hand-edited cases,
as the plan asked), `test_rtt.py` (both fields `None` on every RTT fixture),
`test_models.py` (defaults and serialisation), `test_config.py` and
`test_profiles.py` (the toggles default on, a profile can silence either
independently), and `test_app.py` (`/api/state` carries both fields through
the real LBG fixture). No frontend test harness; verified by hand in the
browser against Addendum 3's checklist — 1080p and 720p, one and two boards,
`Ashford International` and `Abbey Wood via Whitechapel`, a 12-coach
formation, a formation with no loading figures, a stale board — and by
measuring, not eyeballing, that `crt`, `1990s`, `nse`, `splitflap` and
`led-matrix` render exactly as they did before.

## Out of scope

Position on RTT boards (the detail call could carry it, but only fresh, and
a fresh read costs a call per board poll that the allowance cannot spare);
the places a train passes through; positions for anything but the top
service; announcing loading ("the front four coaches are quieter"); formation
changes along the route; the other five themes; a real first-class or
reversed-formation service to check `first_class` and the reversal against.
