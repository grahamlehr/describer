# Addendum 8 — The whole journey, and a formation you can read

Three changes to `thameslink`, and one to failover that the Pi forced.

## The position line was off the board's edge

`.service-detail` and `.service-reason` carried `padding: 0 0.5em`, so at
720p split screen the "Left X" text started 7.7px right of the time, the
ordinal and "Calling at", and the formation stopped 7.7px short of the
countdown. Both are flush now: everything under the top train starts on one
edge and the formation ends level with the status column. Measure it —
`getBoundingClientRect().left` of `.cell.time`, `.service-position`,
`.calling-points-label` and `.service-reason-text` must agree.

## Formation

The strip was twelve 8.5×15.5px boxes squeezed beside the position text, and
it could never show a facility: `.coach` in `base.css` had `overflow: hidden`,
which clipped the first-class "1" drawn above the box, and nothing anywhere
styled `data-toilet`. The overflow is gone (the fill inherits the radius
instead), which un-hides the flag in `modern` too.

`thameslink` now paints its own through `renderFormation`: a line of its own
under the position text, one car per coach sharing the width up to 2.6em,
rounded at both ends, and a wider gap where the unit letter in the coach
number changes (`A6` → `B1`). The coaches arrive front-first (the parser
reverses them on `isReverseFormation`), so the front is the left end, and a
small arrowhead before the first car says so — which is only as true as
that flag, still unverified against a reversed train. Inside each car, over
the fill: "1" for first class, and the wheelchair sign with "WC" for an
accessible toilet, faded and struck through when the feed says
`NotInService`. A mark sits on a full car as often as an empty one, so each
has a rim of the ground colour: the text by `-webkit-text-stroke` painted
under its fill (`paint-order: stroke fill`), the sign by a second mask of
the same drawing stroked wider, laid beneath it. The sign is an inline SVG
used as a mask over a colour — Pi OS Lite has no emoji font. The marks
were first drawn on a line under the cars; they moved inside at the user's
request.

**`modern` draws the same formation.** The drawing lives in `board.js`'s
default `renderFormation` and in `base.css` (`.cars`, `.car-front`, `.car`,
`.car-marks`, `.car-first`, `.car-wc*`, `.car-count`), whose colours are
`thameslink`'s; `modern.css` overrides only the colours, filling a car green,
amber or red by load band as its old boxes did. No theme overrides
`renderFormation` any more, and the loading bands have one home again
(`QUIET_BELOW` / `BUSY_FROM` in `board.js`). In `modern` the formation takes
a line of its own under the position text, because cars small enough to
share that line cannot hold their marks: `.service-detail` is a column in
`base.css`, and `board.js` sets `--detail-share` to 0.9 per line shown — 1.8
with both, 0.9 with one, 0 with neither. That share is only felt by `modern`,
whose `--slot` counts it; `thameslink` keeps the block out of its slot. A board with only a length
(RTT) reads "10 coaches" rather than ten empty outlines.

**Only accessible toilets are marked, by decision.** Standard toilets are
noise at this size, and the feed has nothing about wheelchair spaces;
inferring them from the accessible toilet's coach would be a guess that
could send someone to the wrong door.

`Coach.toilet_in_service` is new, false only on `NotInService`. "Mixed" now
counts as first class. Step 0 captured LBG, BFR, PAD and KGX: BFR and KGX
carry no formation at all, and PAD's GWR trains call every coach
"Standard" and sometimes omit `toilet` entirely — so first class is still
unseen in real data and still unverified.

## Show full journey

`display.themes.thameslink.full_journey` (default off, in `/admin` and
`THEME_OPTIONS`, overridable per profile). When on, the route is the top
train's whole run in place of "Calling at", labelled "Journey":

- `Service.journey` is built by both parsers: previous stops, this station
  (`CallingPoint.here`), subsequent stops. LDBWS already sends both halves on
  the combined board, and RTT's detail call already returns every location,
  so it costs no request. Where a train divides, the first group on each side
  is taken; the others are another train's route.
- `Poller.boards()` empties `journey` on every service but the top one,
  after the filters, so the one that keeps it is the one on screen.
- Stops left behind are dimmed, this station is a white ring, the
  destination keeps the filled dot, and a white arrowhead sits at the top of
  the next stop the train will reach (only stops before this station carry an
  actual time). On RTT nothing carries an actual time, so no arrow.
- It opens on the page the train is on and pages forward from there,
  recomputed on every paint until the first turn because the room is still
  settling while the board loads.
- A service with no journey (an RTT row whose detail call the allowance
  guard skipped) falls back to "Calling at".

The route's key now includes each stop's classes, so a stop being left
repaints; before, an arrival's dimming only moved when the names changed.

## Failover counts consecutive failures, as Addendum 1 always said

On 2026-09-12 RDM answered NBC and returned 500 "The service is currently
unavailable" for SYD. `SourceManager` never reset `_failures` on a primary
success, so SYD's failures between NBC's successes reached `failover_after`
and took both boards to RTT, losing position and formation and spending the
allowance; each recovery probe that landed on NBC switched back only for SYD
to trip it again. A primary success now resets the count: one station the
primary cannot serve goes stale on its own, and the other stays on RDM.
