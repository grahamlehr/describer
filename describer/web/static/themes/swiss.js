/**
 * swiss theme: International Typographic Style. One grotesque, two weights,
 * a strict grid. The module has three jobs: choose the ground (paper or
 * ink), say nothing at all for a train that is on time, and add the small
 * column-heading row that a printed timetable has and the shared DOM lacks.
 */

/** Labels for the heading row, in visual order: time | to | status | platform. */
const HEADINGS = {
  departures: ['Time', 'To', '', 'Plat.'],
  arrivals: ['Time', 'From', '', 'Plat.'],
};

/** U+2032, the prime a timetable uses for minutes. */
const PRIME = '′';

export function attach(boardsEl, options) {
  configure(options);
}

/** The ground is the only option; the palette itself lives in swiss.css. */
export function configure(options = {}) {
  document.body.dataset.ground = options.ground === 'dark' ? 'dark' : 'light';
}

export function detach() {
  delete document.body.dataset.ground;
  for (const el of document.querySelectorAll('.sw-columns')) el.remove();
}

/**
 * An on-time train says nothing: silence is the good news, so the one word
 * on the board that is not silent is the one that matters. A late train
 * says how late, in minutes, where the feed gave a number.
 */
export function statusText(service) {
  switch (service.status) {
    case 'on_time': return '';
    case 'expected':
      return service.delay_minutes > 0
        ? `+${service.delay_minutes}${PRIME}`
        : service.expected_time || '';
    case 'delayed': return 'Delayed';
    case 'cancelled': return 'Cancelled';
    default: return null;
  }
}

/**
 * Column headings, first child of .rows. The cells carry the same classes as
 * a service row's, so the placement rules in swiss.css put them on the same
 * grid tracks. board.js leaves foreign children of .rows alone (1990s relies
 * on the same), so this only has to keep the labels and the position right.
 */
export function afterRender(boardsEl) {
  for (const board of boardsEl.querySelectorAll('.board')) {
    const rows = board.querySelector('.rows');
    let head = rows.querySelector('.sw-columns');
    if (!head) {
      head = document.createElement('div');
      head.className = 'sw-columns';
      for (const cls of ['time', 'destination', 'status', 'platform']) {
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
