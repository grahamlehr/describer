# Addendum 19 — victorian made cheaper and given to the stops; clocks that hold still

Written after victorian (Addendum 18) had run on the Pi 4. Graham's report:
the board was very slow, the clock's seconds most of all; the 0 in every time
was a lighter weight than the other figures; `LONDON VICTORIA VIA CRYSTAL…`
was cut off; the calling points deserved most of the board rather than two
paged lines; and deco's clock shuffled sideways as its digits changed.

## What was slow

Nothing was measured on the Pi: these are the causes found by reading the
page and watching its DOM on a Mac, and all of them were removed.

- **Every row was re-inserted on every state frame.** `renderRows` called
  `rowsEl.append(row)` for each service as its ordering step, so each frame
  took all the rows out and put them back (80 child-list mutations a frame on
  a split screen), and the browser laid out and repainted the whole list.
  Cheap on a flat theme; on victorian that list is a textured frame full of
  shadowed boards. `board.js` now moves a row only when it is out of place.
  This is in the shared code and helps every theme.
- **The clock repainted the wall under it every second.** It sat on the
  nameboard on the brick. It is now a fixed box with `contain: layout paint`
  and `will-change: transform`, so it has its own layer and a tick repaints
  one small enamel plate.
- **The textures were dearer than they looked.** The frame carried four
  gradient layers (two of them 1px grain stripes), the wall two more over the
  brick tile, and every board and stop a sheen. All gone bar the plank seams
  and the brick tile itself.
- **Every board and stop was rotated by a fraction of a degree.** Rotated
  text cannot take the fast path. The hang is gone; the slightly different
  blacks stay.

## The zero

Besley's tabular zero does not follow the weight axis: with
`font-variant-numeric: tabular-nums` every 0 is drawn at regular weight
whatever the weight asked for. It is a fault in the font. victorian no longer
asks for tabular figures anywhere, and overrides `base.css`, which asks for
them on every `.clock`. **Never use `tabular-nums` with Besley.**

## Clocks that hold still

`board.js` now writes the clock as one `<span class="clock-char">` per
character, the colons marked `data-sep`, and rewrites only the characters
that changed. Unstyled, the spans are ordinary inline text, and
`.clock.textContent` is still the time for the themes that read it back
(dial, led-matrix, nse). A theme whose face has no usable fixed-width figures
gives the cells a width:

- **deco**: Josefin Sans has no tabular figures at all (its 1 is half the
  width of its 0), so `tabular-nums` did nothing. Cells of 0.68em, colons
  0.3em.
- **victorian**: cells of 0.7em, colons 0.3em, because of the zero.

## victorian's new arrangement

- **A "via" is a second line** under the name (`.vc-dest` over `.vc-via` at
  0.48em), inside the same board height, instead of trailing the name and
  being cut off.
- **Three trains when there are stops to show.** `afterRender` sets
  `data-vc-stops` on `.rows` from the computed `--calling-share` (after
  `syncRowShares`, so both halves of a split screen agree). With it, the slot
  is `100cqh / (3 + heading + 6 + reason)`, the fourth train onwards is
  hidden, and the calling points have six slots. Without it (no stops, or
  calling points switched off) every configured row is shown as before.
- **The stop boards are lettered as large as will get every stop on at
  once**: `layout` tries 1.3, 1.15, 1, 0.9 and 0.8 of the row's type and
  keeps the first where the lines fit, capped at 4.2vh. Only a route too long
  at 0.8 is paged, on line breaks, every 6 s as before.
- "CALLING AT" is one line above the boards rather than a column beside them.

## Numbers measured

Mock board, row type unchanged from Addendum 18 on a split screen (28.9px at
1080p, 19.3px at 720p; width-bound); one board 49.3px and 32.9px.

| Case | Stops | Scale | Shown at once |
|------|-------|-------|---------------|
| 1080p ×2, mock routes | 2 to 8 | 1.3 (37px) | all |
| 720p ×2, mock routes | 2 to 8 | 1.3 (25px) | all |
| 1080p ×2, Bedford to Brighton | 25 | 0.8 (23px) | 20, then 5 |

Clock box over 7 to 9 s of ticks: one width and one position in both themes.
Mutations per state frame on a split screen: 80 row insertions before, none
after.

## Trap

`base.css` sets `font-variant-numeric: tabular-nums` on `.clock` itself, so a
theme that changes it on `body` has not changed it on the clock.

## How it was checked

In the browser pane against the mock board: victorian at 1920×1080 and
1280×720, one board and two, with and without stop times, a via pushed into a
visible row, a 25-stop route pushed into the block, and a switch to `modern`
and back with nothing left behind. All ten themes were cycled after the
`board.js` change: rows in order, clock right, no console errors.

Not checked: the Pi. Whether it is now fast enough is the open question, and
only the device can answer it.
