/**
 * The palette every theme shares, and the one place that writes it.
 *
 * A theme's stylesheet names its colours in this vocabulary; this module
 * puts a configured colour on the document root, where it beats the
 * stylesheet's own `:root` value, and takes it off again when the colour
 * goes back to unset or the theme is switched away. It is not a theme: it
 * has no DOM of its own and no state beyond what it has written.
 */

/** Role name in config → custom property in the stylesheets. */
export const ROLES = {
  background: '--bg',
  text: '--fg',
  dim_text: '--muted',
  accent: '--accent',
  on_time: '--on-time',
  late: '--late',
  cancelled: '--cancelled',
};

/** Every role. A theme without one of them passes its own shorter list. */
export const ALL = Object.keys(ROLES);

/**
 * Write `colours` on to the root for `roles`, clearing the ones left unset
 * so a colour removed from config goes back to the stylesheet's own.
 */
export function applyColours(colours = {}, roles = ALL) {
  const style = document.documentElement.style;
  for (const role of roles) {
    const property = ROLES[role];
    if (!property) continue;
    const value = colours?.[role];
    if (value) style.setProperty(property, value);
    else style.removeProperty(property);
  }
}

/** Hand every role back to the stylesheet. Themes call this from detach. */
export function clearColours(roles = ALL) {
  const style = document.documentElement.style;
  for (const role of roles) {
    if (ROLES[role]) style.removeProperty(ROLES[role]);
  }
}
