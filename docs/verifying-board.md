# Verifying a board change by hand

_Moved from the old CLAUDE.md._

There is no frontend test harness. Check any change to what the board draws
in the browser pane, and measure it rather than eyeballing it:

- 1920×1080 and 1280×720, one board and two, every theme the change touches.
  Check that unaffected themes render exactly as before by comparing the
  computed `--slot` and row height.
- The names that break layouts, not the fixtures' short ones:
  `London Charing Cross`, `Ashford International`,
  `Abbey Wood via Whitechapel`.
- Truncation:
  `[...document.querySelectorAll(".cell")].filter(c => c.scrollWidth - c.clientWidth > 1)`.
  Not for splitflap: compare each canvas's right edge with its cell's, and
  `canvas.__chars` with the field's `--chars`.
- A stale board, and an RTT board. An RTT board has no position or
  formation, and no calling points when the allowance guard fired; that is
  not a rendering fault.
- If a theme looks like the previous release, the browser cached a file:
  re-fetch with `{cache: 'reload'}`.
