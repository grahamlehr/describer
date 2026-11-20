# Addendum 16 — Three more themes: swiss, linemap, dial

Three themes written as options to choose between, not as a set that must all
stay. Each is a stylesheet and a module in `themes/`; none needs a web font.
Along the way the theme contract gained one hook, and the repo gained a way
to look at a board without a rail API.

## The three

### swiss — International Typographic Style

One grotesque in two weights, black on paper white, one red, hairlines, and
nothing else: no fills, no boxes, no icons.

- **An on-time train says nothing.** `statusText` returns the empty string
  for `on_time`, `+4′` for a train with an estimate (from `delay_minutes`),
  `Delayed` and `Cancelled`. The status column is therefore empty most of the
  day, and anything printed in it is in red and worth reading.
- The weight contrast is the hierarchy: time and platform bold, destination
  regular. The next train is marked by a small red square in the gutter,
  never by a fill.
- Columns are time | destination | status | platform; the operator cell is
  hidden. A heading row (`.sw-columns`, "Time / To / Plat.") is inserted the
  way `1990s` does it and counted in `--slot` as `--heading-share`.
- Every track, the stops and the reason line are sized from `--row-font`
  rather than `em`, so the smaller heading row and the stops land on the same
  tracks as the rows.
- **`ground`** (`display.themes.swiss.ground`, `light` | `dark`) is the one
  option: a white screen in a dark room is unkind. `--rule` is declared again
  under the dark rule, because a `var()` inside a custom property resolves
  where it is declared and the hairlines would otherwise keep the light mix.
- Font stack: Helvetica Neue, Helvetica, Nimbus Sans, Liberation Sans, Arial.
  The Pi is expected to land on Liberation Sans, which is wider; the budget
  leaves room for it (27 was the tightest that fitted in Helvetica Neue).

### linemap — every train as a line on a map

A dark strip map. All the lines leave one vertical station bar on the left.

- **A row is two tiers**: the text, and under it the route. The line's length
  is `max(0.25, sqrt(duration / longest on this board))` of the width, and
  each calling point is a tick at the same square-root scale, from the stops'
  scheduled times. An express is a long clean line; a stopper is a short one
  crowded with ticks. Square root, because one train to Penzance would
  otherwise squash every other line into the left margin.
- A line's colour is a hash of where it goes, modulo eight, so trains to the
  same place share a colour and a train keeps its colour as it moves up.
  A cancelled train is a grey dashed line with no stops.
- **The next train's stops become a labelled strip map** in place of the
  "Calling at" line, three slots high (`--map-share`, derived from
  `--calling-share` so `board.js` hiding the block still hands the height
  back). It draws `service.journey` when there is one: up to four stops
  before this station, this station as a ring, and everything after, with the
  stops already left dimmed and a pulsing marker where `position` puts the
  train. Without a journey (RTT) it draws this station and the stops.
  Too many stops for the width are paged every 6 s with a dashed tail.
- A `via` in the destination is set small after the name rather than
  ellipsised.

### dial — the next hour on a clock

A clock face per board: a minute hand, and a marker on the rim for each train
at the minute it leaves, with its platform inside. The gap between hand and
marker is the wait, and an arc in the accent colour draws it. The middle says
how many minutes to the next train.

- One board: the dial sits left of the list, `min(41vw, 88vh)`. Two boards:
  above it, 42% of the height. Every child of `.board` names its row and
  column (the trap in Addendum 3).
- The status column counts down as the dial does; a late train alternates
  `N min` with `Exp HH:MM` on a 15 s phase, as thameslink does. "Now" is read
  back from `.clock`, so it is the Pi's time, not the browser's.
- A cancelled first train does not get the middle: the label says which
  train was cancelled and the number counts down to the first one running.
- Countdowns are to the minute, so the DOM is written once a minute; only
  the hand's transform moves every second.

## `renderRow`

`board.js` calls `theme.renderRow(row, service, mode)` for each service row,
after its cells are painted and with the row in the tree. Until now a theme
that wanted a row's service had to catch it in `statusText`; linemap needs the
calling points of every row and dial needs every departure time, and neither
is a status. Anything a theme adds to a row must come off in `detach`: rows
outlive a theme switch.

## The mock board

`scripts/mock_board.py` (`preview_start` entry `board-mock`, port 8090) serves
the real `index.html` and `/static` and an invented state over `/api/stream`.
It is standard library only, imports nothing from the app, reads no
credential and opens no outbound connection, so checking a theme costs no RDM
or RTT allowance. `/mock?theme=…&boards=…&mode=…&source=rtt&stale=1&empty=1`
changes what is on screen while it runs. Its trains roll every four minutes
and always include the names that break layouts, a late train, a `Delayed`
one and a cancelled one with a reason.

## Numbers measured

Row type, 8 rows, mock board, Helvetica Neue on a Mac:

| Theme | Budget | 1080p ×2 | 1080p ×1 | 720p ×2 | 720p ×1 |
|-------|--------|----------|----------|---------|---------|
| swiss | 28 | 31.5px | ~57px | 21.0px | 36.9px |
| linemap | 25 | 34.6px | 41.7px | 23.1px | 27.8px |
| dial | 28 | 32.4px | 36.9px | 21.6px | 24.6px |

No cell clips in any of the twelve cases, including
`Abbey Wood via Whitechapel` on a split screen, and no page overflows.
dial was first built at a budget of 25, which ellipsised that name at every
size: its fixed columns and gutter cost 13.2em and the name in bold needs 14.

## Trap found

**A theme that owns the stops is not told when there are none.** `board.js`
empties the list and hides the block, and on a split screen `syncRowShares`
then shows the block again, blank, to keep the two halves level. linemap
kept its last map in a `WeakMap` and repainted from a `ResizeObserver`, so
the previous train's map came back in the blank. It now stamps each map with
the render pass that asked for it and drops any the pass did not ask for.

## How it was checked

In the browser pane against the mock board: 1920×1080 and 1280×720, one board
and two, departures and arrivals, an RTT board, stale, empty, messages, a
reason line, a cancelled row, both swiss grounds, and a switch to `modern` and
back for each (nothing left behind). The truncation test from
`docs/verifying-board.md` returned nothing.

Not checked: a Pi, Liberation Sans, a real feed, the dial's hand crossing the
hour, and frame rate on the device (linemap's marker pulse and the dial's
hand are the only things that move; both are opacity or transform).
