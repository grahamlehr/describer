# Addendum 11 — One structural colour becomes a role after all

Addendum 5 called `--tl-bar` — the background of thameslink's "Later trains"
header — structure, not palette, and left it out of the vocabulary. Asked for
directly, it turns out to be exactly the kind of thing the palette exists for:
a colour someone wants to change without touching a stylesheet. It becomes an
eighth role, `bar`, present only on `thameslink`.

- `ThemeColoursConfig` (`config.py`) gains `bar: Colour | None = None`,
  alongside the other seven. Every other theme's `colours` dict can carry it
  too — the model is still shared — and every other theme still ignores it,
  the same way `thameslink` already ignores a hand-written `on_time`.
- `themes/colours.js`'s `ROLES` maps it to `--tl-bar`; `thameslink.js`'s own
  `ROLES` list is the only one that includes it, so `applyColours` never
  writes it anywhere else.
- **The admin page no longer builds its colour rows from one shared list
  filtered per theme.** `bar` is thameslink-only and nothing else will ever
  share it, so `admin.js` keeps a `ROLE_LABELS` map (every role's name) and a
  `THEME_ROLES` map that names, per theme, exactly the roles and the order to
  show them in — `modern`'s list is the original seven, `thameslink`'s drops
  `on_time` and appends `bar` at the end. `buildColourFields` just walks a
  theme's own list.
- **Its contrast reads against the text colour, not the background.** Every
  other role is checked against `--bg`, because that is what sits behind it;
  the bar is its own ground, and it is `--fg` painted on top of it that has
  to stay legible. `syncColours` special-cases `role === 'bar'` to check
  against `colours.text` instead.
- The mock board in `/admin` grows a "Later trains" strip in the chosen
  colour, but only for a theme whose `effectiveColours()` actually has a
  `bar` — `renderPreview` checks for it before adding the markup, so `modern`
  is unaffected.

## Out of scope

`--tl-rule` (the hairline) stays structural — nobody asked for it, and it is
a `color-mix()` of `accent`, not a flat colour, so it was never a plain
palette entry like `--tl-bar`. The other themes' bars, flaps and casings are
untouched.
