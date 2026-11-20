/**
 * deco theme: Art Deco by restraint. The look is all in deco.css; this module
 * has three jobs the shared DOM cannot do on its own: say the status in the
 * board's fixed vocabulary (the stylesheet capitalises it), set a route
 * small after its destination, and add the row of column headings that a
 * printed departure board has above its trains.
 */

/** Labels for the heading row, in visual order: time | place | platform | status. */
const HEADINGS = {
  departures: ['Time', 'Destination', 'Plat', 'Expected'],
  arrivals: ['Due', 'Origin', 'Plat', 'Expected'],
};

export function attach() {}

/** Everything this theme adds to the DOM goes when the theme does. */
export function detach() {
  for (const el of document.querySelectorAll('.dc-columns')) el.remove();
}

/**
 * The same words as the default, written out so the theme owns them:
 * On time, Exp 16:04, Delayed, Cancelled. CSS sets them in capitals.
 */
export function statusText(service) {
  switch (service.status) {
    case 'on_time': return 'On time';
    case 'expected': return service.expected_time ? `Exp ${service.expected_time}` : 'Delayed';
    case 'delayed': return 'Delayed';
    case 'cancelled': return 'Cancelled';
    default: return null;
  }
}

/**
 * A route ("via Whitechapel") is set small and muted after the name, so
 * "Abbey Wood via Whitechapel" does not cost every other row a fifth of its
 * type.
 * board.js puts the cell back to plain text when the theme is switched away.
 */
export function renderText(cell, text) {
  const match = cell.dataset.field === 'destination' && /^(.*?)\s+(via\s.+)$/i.exec(text);
  if (!match) {
    cell.textContent = text;
    return;
  }
  cell.textContent = `${match[1]} `;
  const via = document.createElement('span');
  via.className = 'dc-via';
  via.textContent = match[2];
  cell.append(via);
}

/**
 * Column headings, first child of .rows. The cells carry the same classes as
 * a service row's, so the placement rules in deco.css put them on the same
 * grid tracks. board.js leaves foreign children of .rows alone (swiss and
 * 1990s rely on the same), so this only has to keep the labels and the
 * position right.
 */
export function afterRender(boardsEl) {
  for (const board of boardsEl.querySelectorAll('.board')) {
    const rows = board.querySelector('.rows');
    let head = rows.querySelector('.dc-columns');
    if (!head) {
      head = document.createElement('div');
      head.className = 'dc-columns';
      for (const cls of ['time', 'destination', 'platform', 'status']) {
        const cell = document.createElement('span');
        cell.className = `cell ${cls}`;
        head.append(cell);
      }
    }
    if (rows.firstElementChild !== head) rows.prepend(head);
    const labels = HEADINGS[board.dataset.mode] || HEADINGS.departures;
    Array.from(head.children).forEach((cell, i) => {
      if (cell.textContent !== labels[i]) cell.textContent = labels[i];
    });
  }
}
