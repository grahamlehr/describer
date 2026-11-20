/**
 * victorian theme: a letterpress railway handbill. The stylesheet does nearly
 * all of it. The module has four jobs: the period's wording for the remarks
 * column, a destination that can carry a dotted leader, the column-heading row
 * a printed timetable has and the shared DOM lacks, and fitting a long station
 * name to the line the printer gave it.
 */

/** Labels for the heading row, in visual order: hour | place | platform | remarks. */
const HEADINGS = {
  departures: ['Hour', 'Destination', 'Plat.', 'Remarks'],
  arrivals: ['Due', 'From', 'Plat.', 'Remarks'],
};

/**
 * Wording: "Punctual" is the Victorian's good news. A train with an estimate
 * says how late it is where the feed gave a number, and otherwise the time it
 * is now expected. The same words are used on an arrivals board.
 */
export function statusText(service) {
  switch (service.status) {
    case 'on_time': return 'Punctual';
    case 'expected':
      return service.delay_minutes > 0
        ? `${service.delay_minutes} min. late`
        : service.expected_time || '';
    case 'delayed': return 'Delayed';
    case 'cancelled': return 'Cancelled';
    default: return null;
  }
}

/**
 * The destination goes in a span of its own so the cell can run a dotted leader
 * from the end of it to the platform; the leader is the cell's ::after in the
 * stylesheet, so nothing here draws it. A route ("via Whitechapel") is set
 * apart in a lighter face. Every other field is plain text. board.js puts
 * the cell back to plain text when the theme is switched away from.
 */
export function renderText(cell, text) {
  if (cell.dataset.field !== 'destination') {
    cell.textContent = text;
    return;
  }
  cell.textContent = '';
  const span = document.createElement('span');
  span.className = 'vc-dest';
  const match = /^(.*?)(\s+via\s.+)$/i.exec(text);
  if (match) {
    span.append(match[1]);
    const via = document.createElement('span');
    via.className = 'vc-via';
    via.textContent = match[2];
    span.append(via);
  } else {
    span.textContent = text;
  }
  cell.append(span);
}

/**
 * Shrinks the station name just enough to fit its line. The name is set in a
 * fat face at a fixed height, and "London St Pancras International" does not
 * fit half a screen at that size; wrapping would cost a second line of height
 * the rows need, so it is scaled instead, never below a floor where it would
 * stop being a headline. --vc-fit is a multiplier read by victorian.css. It is
 * measured again only when the name or the width of the line changes.
 */
function fitStationName(board) {
  const name = board.querySelector('.station-name');
  if (!name) return;
  const key = `${name.textContent}|${name.clientWidth}`;
  if (name.dataset.vcFit === key) return;
  name.style.setProperty('--vc-fit', '1');
  const room = name.clientWidth;
  const need = name.scrollWidth;
  if (room > 0 && need > room) {
    name.style.setProperty('--vc-fit', String(Math.max(0.4, (room / need) * 0.97)));
  }
  name.dataset.vcFit = `${name.textContent}|${name.clientWidth}`;
}

/**
 * Column headings, first child of .rows. The cells carry the same classes as a
 * service row's, so the placement rules in victorian.css put them on the same
 * tracks. board.js leaves foreign children of .rows alone (1990s and swiss
 * rely on the same), so this only has to keep the labels and the position right.
 */
export function afterRender(boardsEl) {
  for (const board of boardsEl.querySelectorAll('.board')) {
    const rows = board.querySelector('.rows');
    let head = rows.querySelector('.vc-columns');
    if (!head) {
      head = document.createElement('div');
      head.className = 'vc-columns';
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
    fitStationName(board);
  }
}

/** Everything this module added is taken off: rows and headers outlive a switch. */
export function detach() {
  for (const el of document.querySelectorAll('.vc-columns')) el.remove();
  for (const name of document.querySelectorAll('.station-name')) {
    name.style.removeProperty('--vc-fit');
    delete name.dataset.vcFit;
  }
}
