# Addendum 5 — The palette a theme hands over

`modern` and `thameslink` take their colours from config. The rest do not, and
the reason is that their colours are not a palette: `crt` has a phosphor,
`splitflap` has flaps, `1990s` has the seven Teletext colours by name, and the
two dot themes have one colour of dot. Renaming those into a semantic
vocabulary would say something untrue about them.

## The vocabulary

Seven roles, named the same in every stylesheet that has them, which is what
lets one config model and one admin panel serve any theme:

| Role | Property |
|------|----------|
| `background` | `--bg` |
| `text` | `--fg` |
| `dim_text` | `--muted` |
| `accent` | `--accent` |
| `on_time` | `--on-time` |
| `late` | `--late` |
| `cancelled` | `--cancelled` |

`modern` already spoke this; `thameslink`'s `--tl-*` were renamed into it. A
theme uses the roles it has: `thameslink` counts down in `--fg` and therefore
has no `on_time`, and its `--tl-bar` and `--tl-rule` stay in the stylesheet
because they are structure, not palette (**partly revised by Addendum 11**,
which gives `--tl-bar` a control after all). **A theme declares its own
roles** in its module and passes that list to `applyColours`, so a role it
does not have is never written.

## How a colour reaches the board

`themes/colours.js` is the only thing that writes one. It is not a theme: no
DOM, no state beyond the properties it has set. A theme calls `applyColours`
from `configure` and `clearColours` from `detach`; the properties land on
`document.documentElement`, where they beat the stylesheet's own `:root` and
come off again cleanly on a theme switch. Nothing in the backend knows a
colour from a flap speed — it is an ordinary theme option on the path config →
`/api/config` → `poller.state()` → SSE → `board.js`.

An unset colour is `null`, not today's hex. **The stylesheet stays the source
of truth**, so a theme that is redrawn later still reaches a board whose owner
once opened the picker.

## Tints are derived, never written twice

`--rule` and the selected-row wash in `modern`, and `--tl-rule` in
`thameslink`, are `color-mix()` of the tokens they are made from. A recoloured
board keeps its hairlines in step with its text and its selection tint in step
with its accent. The Pi runs Chromium 152, so `color-mix` is safe; anything
added in this vein should be mixed rather than spelled out.

## /admin

- The swatch defaults are **read from the theme's stylesheet** at load, not
  copied into `admin.js`. There is no second home for the palette.
- Every colour is measured against the ground, the status colours included,
  and a ratio under 4.5:1 is marked. It still saves: it is your board.
- A mock board row is drawn in the chosen colours, because the alternative is
  a walk to the monitor.
- `readForm` turns a colour field marked `data-unset="1"` into `null`. A new
  field type that has no empty state needs the same treatment.
