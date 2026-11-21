# Addendum 21 — victorian's station clock; one clock a screen

Graham's brief: victorian's clock should be an analogue, mechanical clock
whose seconds tick across; a split screen should show the clock once, in
every theme that showed it twice; the clock may be about twice the height of
the station name, and the name may run to two lines. It should look
traditional and as authentic as it can.

## Decision

- **victorian has a station clock, not an enamel plate of digits.** A round
  dial in a turned mahogany case with a brass bezel, a white enamel face,
  Roman numerals standing on the centre (IIII for four, as dial painters
  wrote it; those round the foot are upside down, as on any Roman dial), a
  minute track heavier at the fives, and blued spade hands on a brass boss.
- **The seconds hand steps** once a second and recoils slightly (a 0.22 s
  transition with an overshooting curve), as a deadbeat escapement lets it.
  The minute and hour hands creep, a tenth of a degree a second for the
  minute hand, as a going train moves them.
- **One clock a screen, in every theme.** `board.js` shows the `.clock` of the
  last board only; on a split screen that is the right-hand board, so the
  clock sits in the screen's own top corner (the foot for 1990s and
  thameslink, whose clocks live there). The hidden clocks are still written
  every tick, for the themes that read the time back.
- **victorian's one clock hangs on the wall**: at the right-hand end of the
  nameboard on one board, and on the iron column between the two nameboards
  on a split screen, each nameboard stopping 0.9vw short of the case.
- **The nameboard is as tall as the clock (17vh, was 9.6vh)** and the name is
  centred on it. A name goes on one line if a squeeze of no more than 15%
  gets it there; otherwise it takes two lines, balanced, at full size; only
  then is it scaled, in steps of 5%, to a floor of 40%.

## How it is built

- The dial is a sibling after `.boards`, `position: fixed`, built once in
  `attach` and removed in `detach`. It is hidden with `.boards[hidden]`
  (display asleep, setup screen) and when `display.clock` is off
  (`data-vc-clock` on `.boards`, set in `afterRender`), and the nameboards
  then take back the room.
- Case and face are one static inline SVG. Each hand is a separate small SVG
  laid over it with `will-change: transform`, turned by a CSS `rotate`, so a
  tick is a compositor transform and repaints nothing. Angles only ever
  increase (the shorter way round from where the hand is), so 59 to 0 does
  not run the hand backwards.
- `themeApi.now()` (new, in `board.js`) is the Pi's time. The theme schedules
  each beat for the next whole second from it, rather than polling `.clock`
  every 250 ms as the older themes do, so the step lands on the second.

## Numbers measured

Mock board, Mac.

| Case | Clock | Row type before | Row type now |
|------|-------|-----------------|--------------|
| 1080p, two boards | 184px, centred at x 974 | 28.9px | 28.9px |
| 1080p, one board | 184px, right | 49.3px | 44.7px |
| 720p, two boards | 122px | 19.3px | 19.3px |
| 720p, one board | 122px, right | 32.9px | 29.9px |

A split screen's type is bound by width, so the taller nameboard costs it
nothing; one board pays about 9%. Seconds-hand steps over four beats landed 5
to 6 ms after the second.

| Name | 1080p ×2 | 1080p ×1 | 720p ×2 |
|------|----------|----------|---------|
| London Bridge | one line, 67px | one line | one line, 45px |
| London Charing Cross | two lines, 67px | one line | two lines, 45px |
| Ashford International | two lines, 67px | one line | — |
| London St Pancras International | two lines, 54px (0.80) | two lines, 67px | two lines, 36px (0.80) |

Other themes, 720p split screen: both halves' `.rows` the same height in
all eleven with one clock showing.

## Traps

- **Every child of `.boards` is a board** to `ensureBoards`, so the dial
  cannot live there; and `.board` is `overflow: hidden`, so it cannot hang out
  of the first board over the second either.
- **`scrollHeight` over-reads Besley**: two lines measured 168px against a
  `clientHeight` of 150, so the first fit shrank every long name to one line.
- **thameslink hid its clock panel with the clock**, which made the left
  half's rows 22px taller than the right's. A board whose clock is hidden
  while another's shows now keeps the panel's room (`visibility: hidden`).
- The mock's `/mock?name=` cannot be set back to blank (`parse_qsl` drops
  empty values); set it to `London+Bridge`.

## How it was checked

In the browser pane against the mock board (which gained `name=` and
`clock=0`): victorian at 1920×1080 and 1280×720, one board and two, clock on
and off, the names above, and a switch to `modern` and back with nothing left
behind. All twelve themes at 1280×720 on a split screen with the clock on;
thameslink also with it off and on one board.

Not checked: the Pi. Three small layers and one transform a second should be
cheap, but only the device can say. Pre-existing and left alone: victorian's
right-hand board on a split screen is narrower by the column, so its row type
is 18.6px against the left's 19.3px at 720p.
