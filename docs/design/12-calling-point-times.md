# Addendum 12 — Each calling point's expected time

`display.show_calling_times` (default **off**), the Darwin "next train" style:
a stop reads "Reading (19:29)" rather than plain "Reading". Off by default
because a time on every stop is characters a page no longer has for stops —
see the truncation history in Addendum 3, which this addendum does not
reopen. No parser or model change and no new API call: `CallingPoint` already
carried `scheduled_time`, `expected_time` and `actual_time` for both feeds:
this only reads them.

## The time rules

`stopTime(point)` in `board.js`, in order:

1. `cancelled` → `null` — no time is printed at all, the same as a bare name
   today. A cancelled stop cannot arrive, so a time on it would be a lie.
2. `actual_time` set → that time. The train has already left this stop, and
   the actual time is the truest thing on offer.
3. `expected_time` an `"HH:MM"` string → that time, and `late` is set when it
   reads later than `scheduled_time` (a plain string compare, since both are
   zero-padded "HH:MM"). This is RTT's only non-null case, and Darwin's own
   estimate once it has one.
4. `expected_time` is `"On time"`, `"Delayed"`, or there is no estimate at
   all → `scheduled_time`, not marked late. "Delayed" means Darwin has
   nothing better to offer, and the booked time is the only honest fallback;
   printing nothing would be worse than printing the time the train was
   supposed to call.

`late` only ever comes from comparing two known clock times (case 3). A
stop that falls back to its booked time (case 4) is never marked late, even
though the train carrying it is by definition not on time — there is no
second time to compare against, so nothing is asserted.

## Where each theme puts it

- **modern, crt, 1990s** — board.js's own `renderCallingPoints` builds
  `"Name (HH:MM)"` once, before pagination, so the default pager's cache key
  (which already includes the joined text) picks up a changed estimate on
  its own. This is the same list of strings paginateCallingPoints has always
  paged; a longer line simply pages sooner.
- **splitflap, nse, led-matrix** — unchanged. All three take the `points`
  argument as opaque strings and flap, page or scroll whatever they are
  given, so `"Abbey Wood (19:47)"` is just a longer name to them. Confirmed
  by reading all three modules: none assumes a stop is a bare station name.
- **thameslink** — the one theme that does not want the time folded into
  the name, because it draws the name ellipsised against the line and the
  time would either be swallowed by that ellipsis or force the whole route
  narrower. board.js therefore does *not* bake the time into `points` for
  this theme's benefit specifically; instead `renderCallingPoints` gained a
  fifth argument, `showTimes` (`display.show_calling_times`, passed straight
  through), alongside the existing `rawPoints` and `service`. `routeStops`
  and `journeyStops` now take the raw stops directly (rather than the
  `points` array of names) and attach a `time: {text, late} | null` to each
  one — recomputing `stopTime` from a small copy kept in `thameslink.js`
  itself, since a theme module cannot import `board.js` (the dependency runs
  the other way). Each `.tl-stop` is now a flex row of `.tl-stop-name`
  (which keeps the ellipsis) and, when there is a time, a right-aligned
  `.tl-stop-time` in `--fg`, or `--late` when `late` is set. The stop's own
  `color` (its `--late`/`--fg`/`--muted` by state) no longer needs to apply
  to the time, because the time span sets its own colour explicitly rather
  than inheriting — both are still overridden together to `--muted` when the
  train above is cancelled, alongside the other cancelled-route rules.
  `__key` already serialises the whole stop list, so a stop's time changing
  repaints exactly as a station being left already did. `measure()`, the
  snap to whole stops, both paging and `scroll_route` all read the rendered
  row height, which the flex row keeps the same as the old block one, so
  none of the four needed touching — confirmed by hand rather than assumed.

## Reaching the option

`DisplayConfig.show_calling_times: bool = False` and the matching
`DisplayOverride.show_calling_times: bool | None = None`, beside
`show_position` — same mechanism, so a profile can turn it on for one window
of the day without touching the base config. It rides `/api/state` for
free: `poller.state()` dumps the whole resolved `DisplayConfig`, so a new
field needs nothing added there, exactly as `show_formation` did not. The
admin checkbox sits in Display → Screen next to "Show formation", and the
profile editor mirrors it in its three usual places (the rendered card, the
`readForm`-equivalent that builds `entry.display`, and `newProfile`'s
starting point) alongside `show_position` and `show_formation`.

## Verifying this by hand

Checked against a live RDM board (`sources.fallback: null`, `RTT_TOKEN`
unset — Addendum 4 stands; this needed no RTT call to verify) carrying a
real delayed-and-partly-cancelled service: the cancelled stops printed no
time on every theme, the delayed stops fell back to their booked time, and
splitflap, nse and led-matrix needed no code change to show any of it. All
seven themes were checked at 1920×1080 and 1280×720, one board and a split
screen with an arrivals board on the other half, with the option on and
off — off changes nothing, which is the point of the default. thameslink's
right-aligned time was confirmed to sit on one edge across every stop by
measuring `getBoundingClientRect()`, not by eye.
