/**
 * 1990s theme: Teletext column headings above the rows, and times written
 * the Ceefax way, four digits and no colon.
 */

const HEADINGS = {
  departures: ['Time', 'Destination', 'Plat', 'Expctd', ''],
  arrivals: ['Due', 'Origin', 'Plat', 'Expctd', ''],
};

export function attach() {}

export function detach() {
  for (const el of document.querySelectorAll('.tt-columns')) el.remove();
}

/** Times lose their colon; everything else is left alone. */
export function renderText(cell, text) {
  const field = cell.dataset.field;
  cell.textContent = field === 'time' || field === 'status' ? text.replace(':', '') : text;
}

/** The Expctd column shows a time wherever there is one, as Ceefax did. */
export function statusText(service) {
  switch (service.status) {
    case 'cancelled': return 'Cancelled';
    case 'delayed': return 'Delayed';
    case 'on_time':
    case 'expected': return service.expected_time || service.scheduled_time || '';
    default: return null;
  }
}

export function afterRender(boardsEl) {
  for (const board of boardsEl.querySelectorAll('.board')) {
    const rows = board.querySelector('.rows');
    let head = rows.querySelector('.tt-columns');
    if (!head) {
      head = document.createElement('div');
      head.className = 'tt-columns';
      for (const cls of ['time', 'destination', 'platform', 'status', 'operator']) {
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
