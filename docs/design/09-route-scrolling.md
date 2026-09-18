# Addendum 9 — Scrolling the route instead of paging it

`display.themes.thameslink.scroll_route` (default off, in `/admin`, in
`THEME_OPTIONS`, overridable per profile) makes the route glide rather than
turn a page. It applies to "Calling at" and to the full journey alike.

- The column glides down to its last stop at `scroll_speed`, rests
  `SCROLL_HOLD_MS` (3 s), glides back to the top at `return_speed`, rests, and
  goes again. The speeds are in **stops a second** (defaults 0.5 and 4), not
  pixels, so they mean the same at 720p and 1080p and on either half of a
  split screen. Down is `linear`, because it is being read; the return is
  `ease-in-out`, because it is not.
- Each leg is one CSS transition on the transform, so the compositor runs it
  and the main thread wakes only at the ends, on a timer per list. Nothing is
  animated from `requestAnimationFrame`.
- The track is still snapped to whole stops, so each end of the scroll rests
  on whole names. There is no "Page 2 of 4" while scrolling.
- The first descent opens where paging would: with the train's next stop near
  the top (the stop it last left above it). After that it runs from the top.
  A new route, which includes a stop being left on a full journey, starts
  again from there.
- `renderCallingPoints`, the `ResizeObserver` and the page timer all call
  `paintScroll`, which leaves a leg alone unless the room or the stop height
  has changed. When one has, the same leg carries on from wherever the column
  has got to. `glide` reads that position **before** setting
  `transition: none`: a style read after it cancels the leg and returns where
  it was going, which made the route leap to the foot whenever the reason line
  appeared mid-scroll.
- `configure` runs on every render pass, so it restarts the lists only when
  one of the three values has actually changed.
