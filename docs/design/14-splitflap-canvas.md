# Addendum 14 — Splitflap flaps are blitted, not laid out

On the Pi the flip was too slow to pass for a machine. Each flap was a
`<span>` whose text changed every step, restarted through `element.animate`
on transform and opacity, so every step made Chromium re-lay-out the cell,
repaint the text run, promote the span to a compositor layer for 40 ms and
demote it again. Measured over CDP in a headless Chrome on the dev Mac
(1920×1080, dpr 1, main-thread time from the start of the frame's callbacks to
the timer that runs after its rendering work):

| | one board, 5 rows | two boards, 11 rows |
|---|---|---|
| DOM flaps | 4.5–5.3 ms a frame, frames dropped to 50–67 ms | not measured |
| canvas tiles | 0.3–0.5 ms a frame, no frame over 2 ms | 0.8–1.0 ms a frame |

A Pi 4B is eight to twelve times slower on one core than that Mac, so the old
figure is 40–60 ms a frame for one board — 15 to 25 fps while anything was
flipping, and worse with two boards. The new one is a few milliseconds.

## What changed

- **Every line of flaps is one `<canvas>`**, in the cell, the stops list or
  the reason line, sized by `splitflap.css` from `--chars` at the pitch the
  DOM flaps measured (0.86em tile, 0.08em gap, 1.32em tall). `TILE_EM` and
  `GAP_EM` in the module are the same numbers; change both. `--chars` is the
  stylesheet's for a board column and is set inline for a paged line, whose
  width is now computed from the host's font size and the pitch rather than
  measured off a rendered flap.
- **Tiles come from a sprite sheet**: the whole drum drawn once per font,
  size and colour, on its flap with the hinge line, and shared by every
  canvas that matches. A step is then three blits at most. The colours of
  the flap halves are `--flap-top`, `--flap-bottom` and `--flap-edge` in the
  stylesheet, read when a sheet is drawn.
- **The flip is three frames a step**, not a frame per refresh: the old top
  flap foreshortened against the hinge, the new bottom flap landing under
  it, then the tile at rest. At 25 steps a second a fourth frame is nothing
  the eye can see and would cost a scaled blit per flap per refresh. Reduced
  motion draws only the third.
- The stepping is still clock-paced with the watchdog behind it, the stagger,
  `MAX_STEPS`, the status alternation, the abbreviation table and the paging
  are unchanged, and `flap_ms` and `click_sound` mean what they did.
- A cell whose colour or face changes (a row turning cancelled) gets a fresh
  sheet and a full repaint; so does every canvas when `document.fonts`
  finishes loading or a `ResizeObserver` reports a new box, exactly as `nse`
  handles its dots.

## Verifying this by hand

`canvas.__to`, `__from`, `__remaining` and `__phase` are the per-flap state;
a flap at rest has `__remaining` of -1. Fixture names still prove little:
`Abbey Wood via Whitechapel` becomes `ABBEY WOOD` by the via rule, and
`London Charing Cross` becomes `LONDON CHARING X`. Checked at 1920×1080 and
1280×720, one and two boards: no canvas extends past its cell, no page
overflow, and a cancelled row's tiles come out in `--cancelled`. Not yet
judged on the Pi itself.
