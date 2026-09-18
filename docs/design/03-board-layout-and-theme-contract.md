# Addendum 3 — Board layout, and what a theme module may override

Written after the splitflap board was found truncating its status column on
the real display. The cause turned out to be structural rather than cosmetic,
so this records the sizing model, the measurements behind its constants, and
the extended theme contract. It supersedes the "Themes" description in the
main body where they disagree.

## What was actually wrong

The splitflap status column showed `EXP 15:` and stopped. Each character is a
flap of `min-width: 0.86em` with an `0.08em` gap, so nine of them need
`8.4em`, and the shared row grid gives the status column `6.8em` — room for
seven. `CANCELLED` was being cut the same way; only `ON TIME` fitted, which is
why it went unnoticed.

Widening that one column is not enough. A splitflap row is 39 fixed-width
characters (5 time + 20 destination + 2 platform + 9 status + 3 operator).
At the shared `2.5vh` type size that is about 39 × 0.94em × 27px ≈ 990px of
tiles in a 906px half-screen. **The row never fitted**; the overflow was
simply hidden. A tile grid cannot reflow, condense or ellipsise, so the type
size has to be derived from the width rather than chosen.

## The sizing model

`.rows` is a size container (`container-type: size`), and everything inside is
expressed against it:

```css
--rows: 8;              /* configured rows, set per board from board.js */
--calling-share: 0.9;   /* the stops block, 0 when it is hidden */
--slot: calc(100cqh / (var(--rows) + var(--calling-share)));
--row-font: min(calc(100cqw / var(--char-budget)), calc(var(--slot) * 0.6));
```

- **Height.** Each row takes `flex: 0 0 var(--slot)`, so the *configured*
  number of services fills the container exactly, on a whole screen or on
  half of one. Sizing from the configured count rather than the count in hand
  keeps the type still as trains drop off the board.
- **Width.** `--char-budget` is how many characters of the theme's own font
  one row must afford. The font is whichever of the two constraints binds.

Measured budgets (1920×1080, two boards, so `100cqw` = 906px):

| Theme | Budget | Why | Resulting type |
|-------|--------|-----|----------------|
| modern | 30 | proportional; the fixed columns are 17.6em and the destination needs ~10em | 30px / 62px |
| crt | 33 | monospace at 0.613em per character, so 20 characters cost 12.3em | 27px / 57px |
| 1990s | 33 | the same monospace grid as crt, under a row of column headings | 27px / 56px |
| splitflap | 39 | one tile per character, and tiles cannot be condensed | 23px / 48px |
| nse | 23 | 34 dot-matrix characters at 0.6em, and no operator column | 37px / 53px |
| led-matrix | 23 | the same 34 characters, on a panel with no printed casing | 39px / 59px |
| thameslink | 24 | proportional, and the featured line is a time plus a destination | 37px / 63px |

(Second figure is the single-board layout.)

### The trap that cost the most time

`.stale` is `hidden` — that is `display: none` — for all but a few seconds a
day. With auto-placement that shifted `.rows` out of the `1fr` track into an
`auto` one, and a size-contained element in an auto track resolves to **zero
height**: `100cqh` became 0, the type became 0px, and the board rendered
blank. Every child of `.board` now names its own `grid-row`. Do not remove
those.

## Theme module contract

`board.js` still owns the data and the DOM. A theme module may export any of:

| Export | Called | For |
|--------|--------|-----|
| `attach(boardsEl, options, api)` | on switch | `api.render()` repaints on the theme's own schedule |
| `configure(options)` | on config change | live theme options |
| `detach()` | on switch away | **must** clear timers and caches |
| `renderText(cell, text)` | per cell | replaces `textContent` |
| `statusText(service)` | per status cell | return `null` to accept the default wording |
| `serviceDetail` | read once | `true` shows the position/formation line; absent (or `false`) keeps it hidden, share 0, on every other theme |
| `positionText(service, mode)` | per detail line | return `null` to accept the default wording |
| `renderFormation(el, formation, length)` | per detail line | replaces the default car strip (Addendum 8); no theme uses it now |
| `renderCallingPoints(list, points, rawPoints, service, showTimes)` | per board | replaces the default paging; `rawPoints` is the service's full `CallingPoint` list, `service` the top service itself (for its `journey`) and `showTimes` is `display.show_calling_times` (Addendum 12), all three ignored by every theme but thameslink |
| `callingPointsLabel(mode, service)` | per board | return `null` to accept "Calling at" / "Called at" |
| `reasonText(service, mode)` | per reason line | return `null` to accept the default wording |
| `renderReason(el, text)` | per board | replaces `textContent` on the reason line |
| `weather` | read once | `true` shows the forecast strip (Addendum 15); absent (or `false`) keeps it hidden, share 0, on every other theme |
| `renderWeather(el, forecast)` | per board | full override of the default strip; `forecast` is the parsed `Forecast` dict or `null`; no theme uses it now |
| `afterRender(boardsEl)` | after a pass | anything left over |

`statusText`, `renderCallingPoints`, `reasonText` and `renderReason` are new.
A theme that wants to repaint on a timer keeps the state itself and calls
`api.render()`; `board.js` then asks it again for the wording, so the timer
and the render never disagree.

## Splitflap specifics

- **Delays alternate.** `statusText` returns `Delayed` and the expected time
  on a 15 s cycle, so nine characters of `Exp 15:23` are never needed. The
  phase is shared by every delayed service on screen, so they flip together.
  `CANCELLED` still needs its nine flaps; the budget above provides them.
- **Names abbreviate before they truncate.** `ABBREVIATIONS` is an ordered
  list applied one rule at a time and only while the name is still too long,
  so `London Charing Cross` becomes `London Charing X` and stops there. A
  trailing `via …` is dropped before any word is cut.
- **Calling points page.** A mechanical board cannot scroll. The stops are
  packed into full-width pages that never split a station name, and turned
  every 6 s. The row width is worked out from the host's own type size and
  the tile pitch (Addendum 14), so it survives a font or size change.

## nse specifics

- Every cell is a `<canvas>`; `nse.js` paints a 5×7 dot font from a column
  bitmap and only repaints a canvas whose discs are turning. A text change is
  swept left to right, `COLUMN_MS` per dot column, paced by the clock so a
  stalled frame catches up. Backing stores follow the CSS box through one
  `ResizeObserver`, so a font or size change redraws crisp.
- The character pitch is `PITCH_EM` (0.1) of the host's font size in the JS and
  `0.6em` per character in the CSS. Change both.
- The operator column is `--chars: 0` and hidden; `renderText` leaves it empty.
- The board is the indicator's casing, and the casing carries only what a
  printer could have put there: the column labels (`TIME`, `TO`/`FROM`,
  `PLAT`, `EXPECTED`) along the top in logo blue, and the logo alone along the
  bottom, in the *last* grid row by CSS. The three-slash logo is a skewed
  gradient on `.board-header::before` with the wordmark on `::after`.
- **Everything that changes is dots.** `board.js` writes the station, mode,
  clock, stops label, messages, the stale warning, the no-services placeholder
  and the reconnecting overlay straight into its own elements with no theme
  hook, so the stylesheet hides all of them and `afterRender` mirrors each into
  a line of dots it owns (`.nse-ident`, `.nse-message`, `.nse-label`,
  `.nse-stale`, `.nse-empty`, `.nse-connection`). Nothing on this sign is
  written rather than flipped: the casing is printed plastic and cannot say
  anything new, so a fault belongs on the matrix like any other message. The
  clock and the reconnecting overlay have no hook at all, so a timer reads
  `.clock` and `#connection` back every `CLOCK_MS`; the overlay especially,
  because `board.js` only toggles its `hidden` flag from the SSE handlers and
  there is no render pass to hang it off.
- The matrix therefore has six claims on its height: `--rows`,
  `--calling-share`, `--heading-share`, `--stale-share`, `--message-share` and
  `--ident-share`. `nse.js` sets `--message-share` and `--stale-share` per
  board, to 0.9 or 0. The fault line takes the top of the matrix, under the
  printed headings; both it and the message line are kept in the tree even when
  empty, because the message line's `margin-top: auto` is what pins it and the
  identification line to the foot of a half-empty board.
- `.connection` is sized `font-size: 0` so only the dots show, which also
  collapses the `em` padding it inherits from `base.css`; both dot themes give
  it a padding in viewport units instead.
- The watchdog must not be re-armed while one is pending. `setTarget` calls
  `start()` for every cell it retargets and the sweep re-enters it on each
  frame, so pushing the deadline back each time meant it never fired — which is
  precisely when frames have stopped and it is needed: the board froze
  part-swept. `armWatchdog` returns early while one is outstanding.
- The stops and the message line turn a page at a time, never splitting a
  name. They cannot scroll: a disc is a fixed place on the board.
- A cell asked for before `nse.css` has arrived (`--chars` computes to the
  empty string) is queued and painted from `afterRender`; `board.js` will not
  ask again for text that has not changed.

## thameslink specifics

- The board is not a list of equals: the top service gets `--feature-share`
  (1.95) slots with its route under it, and the rest are a packed list at
  `--later-share` (0.74) of a slot each under a bar of `--head-share` (0.5).
  What the later trains give up is what the route gets to use. The stops take
  the slack (`flex: 1 1 0`), sized from `--calling-share` so that board.js
  hiding them still hands the height back.
- **The stops are measured against the block, not the track.** The track is a
  `1fr` grid row inside a column flex item, and Chromium sizes that to its
  content rather than to the height flex handed the block, so `clientHeight`
  on the track reads back the whole list. `measure()` works from
  `.calling-points` minus the label's offset instead, then snaps the track to
  a whole number of stops: half a station name under the fold reads as a
  fault, not as a page that continues.
- Paging slides the whole column with a transform rather than swapping the
  text, so the route line runs on across a page turn exactly as it does on
  the real panels, and the page turn stays off the main thread.
- The route takes the leftover height whether it needs it or not
  (`flex: 1 1 0`), so the later trains sit against the foot of the board and
  a train with five stops leaves black between the two. That is wanted, not a
  gap to close: the list holds still from board to board instead of walking up
  and down as the top train changes, and the room is already there when a
  train with a long calling pattern comes along. Do not make that surplus fall
  to the bottom.
- **The trains that are not there still claim their height.** The route is the
  only thing on this board that grows, so a station with four services handed
  it the four missing rows and the blue bar sat a third of the way further
  down than the bar on the other half of a split screen. `.tl-fill` is a
  spacer at the foot of the list of `--missing` × `--later-row`, set by
  `thameslink.js` from the configured `--rows`, so what a short board is short
  of shows as black under its list — where a reader expects it — and the bar
  holds the same line on both halves. It is the *reason the route grows* that
  makes this necessary: the surplus has to be taken away from the route
  before the route can absorb it.
- The stops are re-measured from a `ResizeObserver` on `.calling-points`, not
  only from the page-turn timer. The room for them is settled by flex and is
  still moving while the stylesheet lands and the later trains take their
  share, so the measurement taken during the render is often wrong and only
  the next timer tick corrected it — five seconds of a station name cut in
  half, every time the board loads. Observe the block, never the track: the
  track's height is the one `measure()` sets.
- The next train's block is `flex: 0 0 auto` — exactly its two lines, never a
  share it might not fill. A block sized to a share and centred in it puts
  half its slack between the station name and the train, which reads as the
  board having failed to draw something; sized to a share and aligned to the
  start it merely moves that slack below. `--feature-share` (1.95) stays in
  the slot maths as the *reserve* for the block, and whatever the reserve
  over-provides falls to the route below, which is `flex: 1 1 0` and takes
  it. The block measures 2.04em, which is 1.59 slots where the type is capped
  by the slot, so 1.95 is deliberately more than the block ever needs: the
  reserve is what the *slot* is divided by, and over-providing there sends the
  difference to the route instead of into the type.
- A later train's row is the mean of its own text and the slot it would
  otherwise take: the same line, with half the air. `--later-share` (0.74) is
  then a reserve that no longer matches a row, and that is the point — drop it
  to what a row measures and the slot grows, taking the type and the stops
  with it, so the list tightens and the route gains nothing. Held where it
  was, every pixel a row gives up lands in the route: 4 stops a page against
  3 on a whole screen, and 11 against 8 on half of one.
- The countdown rides the second line, right-aligned against the time and the
  destination and at their size: how long until the train goes is what people
  look up for, so it belongs on the line that says which train, not up among
  the labels. The destination's column is the flexible one, so a long name
  ellipsises before the countdown gives up any room. The first line carries
  only the ordinal and the platform, and the two lines sit on their own
  baselines rather than being centred against each other.
- The status column counts down — "6 min", "Due" — which leaves nowhere to
  print an estimate, so a delayed service alternates the countdown with
  `Exp HH:MM` on the 15 s refresh tick, in one phase shared by the board.
  "Now" comes from the `.clock` board.js keeps against the Pi, not from the
  browser's own clock.
- The clock panel is a mirror. board.js rewrites `.clock` every quarter
  second and compares `textContent`, so a `<sup>` for the seconds inside it
  would be wiped on the next tick; `.clock` is hidden and `.tl-clock` is
  read back on its own timer, as `nse` does.
- `--head-share` is set to 0 from `afterRender` when a board has fewer than
  two services, so the bar claims no height with nothing under it.

## Two dot-matrix themes, one font

`themes/dotmatrix.js` holds the 5x7 glyphs, `columnsFor`, the abbreviation
table, `fit`, `normalise` and `paginate`. It is pure: no DOM, no state. Both
dot themes import it, so the letters can never drift apart, and each still
owns its canvases and decides what one dot looks like.

The two differ in the ways the real machines did, and those differences are
the point of having both:

| | `nse` (flip-dot) | `led-matrix` (LED) |
|---|---|---|
| A change | sweeps column by column, discs drawn edge-on mid-flip | simply appears |
| Too long for the line | **pages**, every 6 s, never splitting a name | **scrolls**, a dot column every 45 ms |
| Frame loop | `requestAnimationFrame` plus a watchdog, for the sweep | none at all; painting is synchronous |
| Dots | reflective discs, cream on black | amber cores with a bloom, over the dark glint of the unlit ones |

A disc is a fixed place on the board and cannot slide sideways, so `nse` must
page; an LED panel is free to scroll. The LED paint is three `Path2D` fills
(dark, bloom, core) costing about 0.7 ms for a full-width scrolling line on a
dev Mac, and there are at most two such lines per board.

Both mirror the same set of text: the two carry the station, mode and clock in
different places (`nse` along the foot of the matrix, `led-matrix` along the
top), but the stale warning, the no-services placeholder, the stops label, the
messages and the reconnecting overlay are lit on both. `led-matrix` hides
`.stale` for the same reason `nse` does — the panel is the whole screen, so
there is no surface left to write a fault on that is not made of LEDs.

`led-matrix` has no config block. Its field would have to be named
`led-matrix`, which is not a Python identifier, and amber is the only colour
those panels came in. The palette is two constants at the top of the module.
`1990s` has none either, for the first of those reasons and because the seven
Teletext colours are not ours to choose. `ThemesConfig` therefore carries five
of the seven themes; a theme whose name is not an identifier cannot have one.

## `hidden` needs saying when the theme sets a display

`base.css` gives `.calling-points` `display: flex`, and an author `display`
beats the `hidden` attribute's UA rule, so `wrap.hidden = true` did nothing:
the block stayed laid out, on `grid-row: 3` of `.board`. Every theme got away
with it because the element then overlapped whatever else was in that row. It
only became visible in `nse` once the fault line let its stale row collapse and
the header moved up, at which point a stray "CALLING AT" appeared under the
logo. `.calling-points[hidden] { display: none; }` is now in `base.css`; any
new rule that sets `display` on an element `board.js` hides needs the same.

## Calling points belong to their service

They now render inside `.rows`, directly under the top service, in every
theme — they describe that train, not the board. `board.js` moves the block
after the first row on each pass and gives it a share of the height, or none
when it is hidden.

The block is one line: the label and the stops together at 0.9 of the row's
type, so the stops read a size below the destination. The marquee is gone.
`board.js` packs the stops into pages that fit the line, never splitting a
name, and turns a page every 5 s; splitflap still does its own paging through
`renderCallingPoints`. Pages are measured by painting candidates into the
list, so the font is part of the cache key: a theme's stylesheet and web font
arrive after the switch, and a page measured in the old face does not fit the
new one. `board.js` therefore re-renders on the stylesheet's `load` and on
`document.fonts` `loadingdone`.

## Why a train is late or cancelled

Both feeds carry the reason as text (`Service.cancel_reason`,
`delay_reason`), worded as a sentence written to follow the status:
"This is due to a shortage of train crew". `board.js` strips that lead-in and
joins the rest on to what the train is doing, giving the wording the
announcements use:

> The 15:24 to Abbey Wood via Whitechapel is delayed due to a fault with the
> signalling system

A reason worded some other way is printed after a colon rather than mangled.

- **The line names its train**, with the time and where it is going — or where
  it is coming from, on an arrivals board. It does not always sit under the
  train it is about, and a board is read from across a platform, so a bare
  "Delayed due to…" under a list of eight trains says nothing useful.
- **It belongs to a train, not to the board.** The line renders inside
  `.rows`, under the top service and under its stops, exactly as the calling
  points do. The board is describing its top service, so that is whose reason
  this is; when that train is running normally and a later one is not, the
  later one takes the line, and the sentence says which train either way.
- Only the reason that matches the state the train is *in* is shown: a
  service running to time may still be carrying the reason it was late an
  hour ago, and printing that is worse than printing nothing.
- `--reason-share` is one more claim on the height, and `board.js` sets it per
  board to 0.9 or to 0. Most of the day it is 0 and the line costs the board
  nothing, so **a theme that writes its own `--slot` must add it** — `1990s`,
  `nse` and `led-matrix` all do. `thameslink` deliberately does **not**: see
  its own section for why.

### It is a sentence, so it never simply fits

Nothing here may be trusted to fit a board line at a size worth reading, and
a reason cut off at the margin is worse than useless — the half that matters
is usually the end of it. Every theme therefore pages or scrolls the whole
text, the way that machine would have:

| Theme | What it does |
|-------|--------------|
| modern, crt, 1990s, thameslink | `board.js` packs it into pages that fit and turns them with the stops, every 5 s |
| splitflap | flaps it, paged with the stops on the drum's own 6 s |
| nse | pages it on the matrix — a disc is a fixed place on the board |
| led-matrix | scrolls it, a dot column at a time — an LED panel can |

The default pager measures by painting candidates into `.service-reason-text`,
a box inside the line that grows and shrinks against everything else on it, so
what it reports is the room a theme's own prompt or label has left it, in that
theme's font and capitals. Never split a word across a page.

Colour is each theme's own: `modern` takes the colour of the status it
explains, `crt` a terminal prompt and the cancelled blink, `1990s` Teletext
green and red, `thameslink` amber and red. The dot themes have one colour of
dot, so what marks the line out there is where it is.

## Verifying this by hand

- The animation needs `requestAnimationFrame`, which a hidden browser pane
  does not run: the flaps freeze mid-alphabet (the watchdog still steps them,
  four times a second). A headless Chrome driven over CDP does deliver frames
  at 60 Hz and is how Addendum 14 was measured.
- `setInterval` is throttled in a background tab, so the 15 s status cycle and
  the 6 s page turn only run while the pane is displayed.
- Theme CSS was cached hard, which cost a deployment: the Pi drew a new theme
  module against the previous release's stylesheet. `/static` and both pages now
  send `Cache-Control: no-cache`, so the browser revalidates every file and the
  ETag turns that into a 304. A browser that cached a file *before* that change
  still holds it, so clearing `~/.cache/chromium` once is what unsticks a Pi;
  in a pane, re-fetch with `{cache: 'reload'}` or append a query string.
- Truncation is easier measured than seen:
  `[...document.querySelectorAll(".cell")].filter(c => c.scrollWidth - c.clientWidth > 1)`.
  That test reads a splitflap cell wrong: its flaps live on one canvas, so
  the cell never overflows. Compare the canvas's `getBoundingClientRect()`
  right edge with its cell's instead, and check `canvas.__chars` against the
  `--chars` for its field — a mismatch is the bug below, not a long name.
- A board with fixture data proves very little. The recorded fixtures hold
  short destinations; the names that break a layout are `London Charing Cross`,
  `Ashford International` and `Abbey Wood via Whitechapel`. Test with those.
- An RTT board with no calling points usually means the rate-limit guard
  dropped the detail calls (see Addendum 2), not a rendering fault.

## A row must be in the tree before its cells are painted

`renderRows` used to build a row from the template, paint all five cells, and
`append` it afterwards. A detached element has no computed style, so
`getComputedStyle(cell).getPropertyValue('--chars')` came back empty and
`renderText` fell back to the length of the text it was handed. Every splitflap
cell therefore got a grid exactly as wide as its own text: `OXFORD` was six
flaps, `ON TIME` was seven, and `ABBEY WOOD VIA WHITECHAPEL` was twenty-six,
which is where the overflow came from. Nothing was ever padded, and the
abbreviation table never fired, because a name is never longer than itself.

`append` now happens first; it still doubles as the ordering step, since
appending a row that is already in `.rows` moves it. `setText` short-circuits
on `dataset.rendered`, so a cell painted wrong once stays wrong — anything
reading layout out of CSS has to run against a connected node. The canvas
that replaced the flap children (Addendum 14) is sized from the same computed
style, so the rule stands.

## Open items

- Verified at 1280×720 and 1920×1080, one and two boards, all seven themes:
  rows fill 100% of the height, no page overflow in either axis, and no cell
  clips except in `modern` and `crt` (see below). The model is width-bound at
  both sizes and the two are proportional, so 720p is not a separate case —
  what fits at 1080p fits at 720p, smaller. `thameslink` is the exception to
  the width-bound half of that: its type is capped at `0.78` of a slot, which
  binds on a whole screen and not on half of one, so both counts were checked
  rather than inferred. It fits `Abbey Wood via Whitechapel` at either.
- `modern` and `crt` still ellipsise a destination past about 10.4em
  (`Abbey Wood via Whitechapel`, `London Charing Cross` on a split screen).
  That is the proportional themes working as designed, but they have no
  equivalent of splitflap's `ABBREVIATIONS`; giving them one would buy back
  several characters.
- Splitflap at 720×2 boards renders at 15.5px. It measures correctly and it is
  what the 39-character budget allows in half of 1280, but it wants judging on
  the real screen, not in a browser pane.
- `0.6` in `--row-font` and `0.9` for `--calling-share` are judged by eye, not
  derived. `--calling-share` was 1.3, which reserved 89px for 53px of label and
  stops; 0.9 is the measured content plus air, and it is the safe ceiling only
  because the block's type is capped at half the row's. They are still the
  first things to adjust if the board looks wrong.
- `--calling-share` has a second home in `board.js`, which sets it per board to
  the same number or to 0. Change both.
