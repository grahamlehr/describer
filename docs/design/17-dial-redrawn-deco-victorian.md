# Addendum 17 — The dial redrawn, and two period themes: deco, victorian

Written after the first dial (Addendum 16) had been on the real screen for an
afternoon. Its section there is superseded by this one; the rest of 16 stands.

## dial, redrawn

What was wrong, in Graham's words: the clock idea and the look were right, but
the text inside the dial ("next train / Due / Portsmouth H…") was not useful,
and nor were the circles of different sizes with platform numbers in them.
Both faults were the same one: the dial was being asked to carry words, and a
circle has no room for a station name.

- **The dial is now a real clock.** Hour, minute and second hands from a hub,
  sixty ticks, the numerals 1 to 12, and nothing written in the middle. It
  tells the time, which the first one did not.
- **Trains on the rim say one thing: when.** One identical slim bar per train
  at the minute it leaves, with colour as the only variable (next running
  train in the accent, late in amber, cancelled a hollow outline). No sizes,
  no numbers. A second train in the same minute sits on an outer ring. The
  arc from the minute hand to the next train is kept and drawn bolder.
- **The words moved out to a panel** beside the dial (under it on a single
  board): `Next train`, the wait in minutes, the destination in full on up to
  two lines, the time and platform, and `then 7, 11 and 15 min`. Its type is
  sized in `cqmin` of its own box, so nothing in it is ever truncated.
- **"Next" is the soonest running train, not the first row.** A train four
  minutes late is overtaken by the one behind it, and "5 min, then 4, 8 and
  12" read as a fault. A cancelled first train is named on a line of its own
  above the panel.
- Two boards: the dial and panel share a band of 41vh. One board: the left
  column is 50vh wide, dial over panel. The hands are placed every 500 ms so
  they trail the `.clock` text by at most half a second.

## deco — a 1930s picture palace

Black lacquer, gold, cream. Asked for "all out" and "quirky".

- A double gold frame with stepped corners (one SVG tile as a `border-image`),
  the station name in Limelight capitals on a conic-gradient sunburst between
  two fans, the clock in an octagonal plaque, platform numbers in gold
  octagons, a fluted pillar between two boards, a stepped skyline at the foot.
- The next train sits between double rules with drawn chevrons, and a gold
  glint crosses it every 11 s: transform and opacity on one element, and off
  under `prefers-reduced-motion`.
- Status keeps the ordinary words in capitals: `ON TIME`, `EXP 16:24`,
  `DELAYED`, `CANCELLED`.
- **A "via" is set small after the name.** Tracked capitals are wide:
  `ABBEY WOOD VIA WHITECHAPEL` at full size needed 18em and forced a budget
  of 35, which is 24px type in an 87px row at 1080p. Set as a supporting act
  it costs nothing, the widest name becomes `ASHFORD INTERNATIONAL` at
  14.2em, and the budget is 31.

## victorian — a letterpress handbill

Aged paper, black ink, one vermilion, and a different typeface on every line,
as a jobbing printer would have set it.

- "The Public are respectfully informed of the / DEPARTURES / from / LONDON
  BRIDGE", a rule carrying a small locomotive, the clock in a "Railway Time"
  cartouche, a printer's border with corner rosettes, "By Order." at the foot.
- Rows are a timetable: the time in Abril Fatface, the destination in Old
  Standard bold, a dotted leader running to the platform. The leader is the
  cell's `::after`, so there is no opaque patch over the paper.
- A drawn pointing hand marks the next train. The hand, the locomotive and
  the rosettes are SVG masks over a colour: Pi OS Lite has no font with a
  manicule or a fleuron in it.
- Status is in period but unambiguous: `Punctual`, `4 min. late`, `Delayed`,
  and `Cancelled` as a tilted rubber stamp. Late figures are roman, because
  Old Standard's italic 4 reads as struck through.
- The station name is scaled to fit its line by `victorian.js` (`--vc-fit`,
  floor 0.4) at a fixed line height, so a long name never costs the rows a
  second line. The paper is static gradients only.
- It is a light theme with no dark ground. Use a profile to change theme at
  night if that matters.

## Fonts are now shipped

Neither period theme survives a fallback to Liberation Sans, so their faces
are in `web/static/fonts` and served by the Pi: Limelight and Josefin Sans
(deco); Abril Fatface, Old Standard TT regular, bold and italic, and Rye
(victorian). Seven files, 121 KB, the Latin subsets Google Fonts serves, all
under the SIL Open Font License 1.1 (`fonts/OFL.txt` has the licence and each
copyright notice). Every `@font-face` is `font-display: block`: a board that
paints in Arial for a moment and then reflows would repaginate its stops.
A test asserts each file is served.

## Numbers measured

Row type, 8 rows, mock board:

| Theme | Budget | 1080p ×2 | 1080p ×1 | 720p ×2 | 720p ×1 |
|-------|--------|----------|----------|---------|---------|
| dial | 28 | 32.4px | 45.7px | 21.6px | 30.5px |
| deco | 31 | 27.6px | ~51px | 18.4px | 33.1px |
| victorian | 27 | 32.4px | 44.0px | 21.6px | 26.3px |

No cell clips in any case and no page overflows. deco on half of a 720p
screen is the smallest type of the three and wants judging on the real
display.

## How it was checked

In the browser pane against the mock board (Addendum 16): 1920×1080 and
1280×720, one board and two, with stale, messages, a reason line, a cancelled
row and a late one; arrivals and RTT by the builders of each theme. Each was
switched to `modern` and back with nothing left behind. The dial's hands and
bars were compared with the `.clock` text and the trains' minutes.

Not checked: the Pi (frame rate with deco's glint and the dial's second hand
in particular), a real feed, and the dial's hands crossing twelve.
