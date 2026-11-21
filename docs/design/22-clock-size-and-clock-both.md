# Addendum 22 — a bigger station clock; a clock on both boards by choice

Graham, after Addendum 21 reached the board: the victorian clock is right
but could be at least half as big again; and for the themes without an
analogue clock, the modern ones especially, whether a split screen shows the
clock on both boards or on one should be a screen setting.

## Decision

- **victorian's clock is 26vh across (was 17vh)**, 153%. The nameboard is
  still as tall as the clock, so it grows with it, and the name is lettered
  at 8vh (was 6.2vh) with the same fitting rules.
- **`display.clock_both: bool = False`**, beside `display.clock`, on the
  Display tab as "Clock on both boards" and overridable by a profile like its
  neighbours. Off, a split screen shows one clock, on the last board
  (Addendum 21). On, every board shows its own.
- **victorian ignores it.** Its one dial hangs on the column between the two
  boards; two would not be a station.
- Off is the default because one clock was what Addendum 21 was asked for.

## Numbers measured

Mock board, Mac.

| Case | Clock | Row type, Addendum 21 | Now |
|------|-------|-----------------------|-----|
| 1080p, two boards | 281px | 28.9px | 28.9px |
| 1080p, one board | 281px | 44.7px | 39.1px |
| 720p, two boards | 187px | 19.3px | 19.3px |
| 720p, one board | 187px | 29.9px | 26.1px |

A split screen is still bound by width. One board pays for the taller
nameboard: 21% below where it stood before Addendum 21 (49.3px at 1080p).
The stop boards have less height, so they settle a size smaller (0.9 to 1 of
the row type where they were 1.3 on the mock's eight-stop routes); every
stop still showed at once.

## How it was checked

Mock board (`/mock?both=1` is new): victorian at 1920×1080 and 1280×720, one
board and two, the long names; modern and thameslink on a split screen with
`both` on and off (two clocks, then one, the halves' `.rows` level each
time); victorian with `both` on (still one dial). `test_state_carries_clock_both`
covers the option reaching the board's state.

Not checked: the Pi, and `/admin` in a browser (the mock does not serve it;
the checkbox is swept by name like the others on the tab).
