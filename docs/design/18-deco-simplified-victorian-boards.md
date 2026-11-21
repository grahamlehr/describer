# Addendum 18 — deco simplified, victorian as hung boards on brick

Written after both period themes (Addendum 17) had been seen on the real
screen. Graham's verdict on both: "a bit too much of a pastiche". The deco and
victorian sections of 17 are superseded by this note; its dial section and its
reasoning for shipping fonts stand.

## deco — Art Deco by restraint

Asked for: "much simpler and more elegant". Everything that was drawn has
gone: the sunburst, the fans, the stepped frame, the octagons, the pillar, the
skyline, the chevrons, the moving glint, and Limelight with them.

- What is left is the proportion and the type. Josefin Sans only: the station
  name in light capitals at 0.2em tracking, times in gold, destinations in
  ivory at weight 600.
- **One ornament**: a fine double gold rule under the header, the upper line
  the heavier. It is two borders with a gap, not a gradient.
- **Only trouble takes colour.** `ON TIME` is muted; a late train and a
  cancelled one are the only coloured words on the board.
- The next train is marked by a short gold bar in the gutter. No fill, no
  frame.
- **Destinations are mixed case.** Tracked capitals were costing the board
  its type size: the budget was 31 and is now 25, so a split 1080p screen
  reads at 35px where it read at 28px. A "via" is still set small.
- No animation, no gradients, no clip-paths, no SVG.

## victorian — a train indicator on a brick wall

_The hang, the grain, the two paged lines of stops and the trailing "via"
described below were changed by Addendum 19 after a day on the Pi._

Asked for: wooden boards with the calling destinations hung on them, hand
sign-written, Victorian red brick behind, and the name of the station, with
the Great Western, the Great Northern and the Southern at their height in
mind.

### What the research found

It was thin, and most of it is recollection rather than record:

- Stations had wooden **train indicators** with removable painted boards,
  one per destination, slotted or hung by staff before each train. They were
  called **finger boards** because they pointed at the train. The examples
  people remember are black with white lettering; the holders were sometimes
  lettered "Next Train". They lasted longest on the Southern.
  (RMweb, "Wooden departure boards at the platform"; GW Railwayana Auctions'
  finger-board listings.)
- The Science Museum Group holds a wooden platform indicator, "All stations
  to Penrith", white on blue, 838 × 502 × 12 mm, and a Great Western wooden
  train indicator board of about 1930. Neither record describes the
  lettering.
- Great Western nameboards are described by modellers as white letters on a
  black ground, and GWR station woodwork as "light stone" and "dark stone"
  rather than the chocolate and cream British Railways used later. A London &
  South Western noticeboard of 1918 is black with a red header and white
  block capitals.
- Nothing found describes a Great Northern indicator, and nothing is dated
  before 1900. So the theme is a plausible working object, not a copy of a
  named one, and carries no company's name or device.

### What was built

- **The wall** is running-bond brick, a small SVG tile as a background: CSS
  gradients cannot offset alternate courses by half a brick. A brick is 3.2vh
  high and no text sits on it.
- **The nameboard**: a black running-in board in a dark stone frame with a
  light stone bead, the station name in white capitals with a hard block
  shade (an offset `text-shadow` with no blur), scaled to fit by
  `fitStationName`. The clock is a blue enamel plate beside it.
- **The indicator** is `.rows` itself, a timber frame with `DEPARTURES`
  sign-written on its header rail. **Each train is a black board** hung on
  two brass pins, tilted by up to ±0.35° and tinted a slightly different
  black, both from a hash of the row's id so a board hangs the same way on
  every render. Time in a ruled panel, platform on a white-on-blue plate.
- **An on-time train carries no status at all**, as a hung indicator showed
  nothing when the train was right time. A late one has an amber slip
  (`LATE 5 MIN`), a cancelled one a red slip and its lettering struck out.
- **The calling points are little boards of their own**, one per stop, hung
  in two lines on a rail under the next train (`renderCallingPoints`, two
  slots high through `--stops-share`). More than fit are paged every 6 s,
  whole boards only, on line breaks.
- The reason, messages and the stale warning are buff or red slips tacked to
  the frame. Two boards are parted by a cast-iron column.
- The face is Besley, a Clarendon: the "Egyptian" a Victorian signwriter and
  a wood-type printer both reached for.

### Trap

`.rows` now has padding, because it is the frame. The slot divides the
container's *content* height, so sum checks must compare against that and not
`clientHeight`.

## Fonts

Six files went with the designs that used them (Limelight, Abril Fatface,
Old Standard TT ×3, Rye) and one arrived (Besley, 36 KB, SIL OFL 1.1). The
shipped set is now `bedstead`, `josefin-sans` and `besley`; `fonts/OFL.txt`
and the test that each is served were updated to match.

## Numbers measured

Row type, 8 rows, mock board:

| Theme | Budget | 1080p ×2 | 1080p ×1 | 720p ×2 | 720p ×1 |
|-------|--------|----------|----------|---------|---------|
| deco | 25 | 35.3px | 53.1px | 23.6px | 35.4px |
| victorian | 30 | 28.9px | 45.0px | 19.3px | 30.0px |

No cell clips and no page overflows in any of the eight cases. victorian on
half of a 720p screen is width-bound at 19px: heavy capitals are wide, and
`ASHFORD INTERNATIONAL` has about 8px to spare at 1080p.

## How it was checked

In the browser pane against the mock board: 1920×1080 and 1280×720, one board
and two, with a cancelled row, a late row and a reason line; arrivals, RTT,
stale, empty, messages and stop times by the builder of each. Each was
switched to `modern` and back with nothing left behind.

Not checked: the Pi, and whether the brick and grain cost it anything to
paint (they are static backgrounds); the tilted boards at pixel level; a real
feed.
