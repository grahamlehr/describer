/**
 * modern theme: no animation, so the module exists only to carry the
 * palette. A new theme may copy this and fill in the other hooks:
 * attach, configure, detach, renderText, statusText, renderCallingPoints,
 * reasonText, renderReason, afterRender.
 */

import { applyColours, clearColours } from './colours.js';

/** There is room for the position/formation line, so board.js shows it here. */
export const serviceDetail = true;
/** A full-width band at the foot of the board; see base.css's .weather. */
export const weather = true;

export function attach(boardsEl, options) {
  configure(options);
}

export function configure(options = {}) {
  applyColours(options.colours);
}

export function detach() {
  clearColours();
}
