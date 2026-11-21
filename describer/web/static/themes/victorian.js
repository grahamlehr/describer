/**
 * victorian theme: a station train indicator on a brick wall.
 *
 * The stylesheet draws the wall, the frame and the boards. This module has
 * five jobs the shared DOM cannot do on its own:
 *
 *  - the wording on the slips hung beside a train (nothing at all when it is
 *    on time, as a hung indicator showed nothing for a train that was right);
 *  - a destination with its "via" painted small on a second line;
 *  - a slightly different black for every board, from a hash of the id it
 *    carries, so it keeps its shade as it moves up the frame;
 *  - the calling points as little boards of their own, in a block six slots
 *    high under a one-line label (the stylesheet shows three trains, not the
 *    configured number, while the stops are up; afterRender sets
 *    data-vc-stops for it). The boards page by whole lines on a timer only
 *    when they will not all fit (the block owns them: board.js hands over the
 *    list, as it does to linemap);
 *  - the header rail's wording, the column headings and a station name fitted
 *    to its board.
 *
 * Everything added to the page is removed in `detach`: rows and `.rows`
 * outlive a theme switch.
 */

/** How long one page of calling-point boards holds before the next. */
const PAGE_MS = 6000;
/** Stop-board type as a multiple of the row's, tried largest first. */
const STOP_SCALES = [1.3, 1.15, 1, 0.9, 0.8];
/** Besley's own letters, so a board's black is never quite the same twice. */
const TINT_STEPS = 7;

/** Column headings, in the order of the row's cells: nothing over the status. */
const HEADINGS = {
  departures: ['Time', 'To', 'Platform', ''],
  arrivals: ['Time', 'From', 'Platform', ''],
};

/** State per `.calling-points-list`: what it was built from and which page. */
let stops = new WeakMap();
/** The lists with boards, so the page timer finds them; pruned when they go. */
const stopLists = new Set();
let pageTimer = null;
let observer = null;
let nameObserver = null;
/** Counts render passes, so a list nobody asked for in the last one is cleared. */
let pass = 0;

export function attach() {
  pageTimer = setInterval(turnPages, PAGE_MS);
  // The room for the boards is settled by flex while the stylesheet and the
  // font land, so measure again the moment the block moves rather than on the
  // next page turn.
  observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const list = entry.target.querySelector('.calling-points-list');
      if (list) layout(list);
    }
  });
  // The name's room moves when the clock plate or the board does; refit then,
  // not only on the next state frame.
  nameObserver = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const board = entry.target.closest('.board');
      if (board) fitStationName(board);
    }
  });
}

export function configure() {}

export function detach() {
  clearInterval(pageTimer);
  pageTimer = null;
  observer?.disconnect();
  observer = null;
  nameObserver?.disconnect();
  nameObserver = null;
  stopLists.clear();
  stops = new WeakMap();
  for (const el of document.querySelectorAll('.vc-columns')) el.remove();
  for (const rows of document.querySelectorAll('.rows')) {
    delete rows.dataset.vcMode;
    delete rows.dataset.vcStops;
  }
  for (const row of document.querySelectorAll('.row')) {
    row.style.removeProperty('--vc-tint');
  }
  for (const cell of document.querySelectorAll('.cell')) cell.classList.remove('vc-two-line');
  for (const list of document.querySelectorAll('.calling-points-list')) list.textContent = '';
  for (const wrap of document.querySelectorAll('.calling-points')) wrap.style.removeProperty('--vc-stop-scale');
  for (const name of document.querySelectorAll('.station-name')) {
    name.style.removeProperty('--vc-fit');
    delete name.dataset.vcFit;
  }
}

/* ------------------------------------------------------------------ wording */

/**
 * What the slip says. An on-time train has no slip, so the cell stays empty
 * and the stylesheet hides it. A train with an estimate says how late it is
 * where the feed gave a number, and otherwise when it is now expected.
 */
export function statusText(service) {
  switch (service.status) {
    case 'on_time': return '';
    case 'expected':
      return service.delay_minutes > 0
        ? `LATE ${service.delay_minutes} MIN`
        : service.expected_time ? `EXP ${service.expected_time}` : '';
    case 'delayed': return 'DELAYED';
    case 'cancelled': return 'CANCELLED';
    default: return null;
  }
}

/** The stops are "Calling at" on a departure and "Called at" on an arrival. */
export function callingPointsLabel(mode) {
  return mode === 'arrivals' ? 'Called at' : 'Calling at';
}

/**
 * A destination with a "via" is two lines: the name in a span, and the via
 * small under it (the cell gets vc-two-line). One without is plain text. "-" is board.js saying there is no platform yet: the
 * enamel plate is then empty and hidden. board.js puts the cell back to plain
 * text when the theme is switched away from.
 */
export function renderText(cell, text) {
  const field = cell.dataset.field;
  if (field === 'platform') {
    cell.textContent = text === '-' ? '' : text;
    return;
  }
  if (field !== 'destination') {
    cell.textContent = text;
    return;
  }
  cell.textContent = '';
  const match = /^(.*?)\s+(via\s.+)$/i.exec(text);
  if (!match) {
    cell.classList.remove('vc-two-line');
    cell.textContent = text;
    return;
  }
  cell.classList.add('vc-two-line');
  const dest = document.createElement('span');
  dest.className = 'vc-dest';
  dest.textContent = match[1];
  cell.append(dest);
  const via = document.createElement('span');
  via.className = 'vc-via';
  via.textContent = match[2];
  cell.append(via);
}

/* -------------------------------------------------------------------- hang */

/** A stable hash of some text: the same name always hangs the same way. */
function hash(text) {
  let h = 5381;
  const value = String(text);
  for (let i = 0; i < value.length; i++) h = ((h * 33) ^ value.charCodeAt(i)) >>> 0;
  return h;
}

/**
 * Each board is a hair off black. Set once per row,
 * from its id, so it does not change between renders or when the board moves.
 */
export function renderRow(row) {
  const id = row.dataset.id || '';
  if (!row.style.getPropertyValue('--vc-tint')) {
    const step = hash(`${id}:tint`) % TINT_STEPS;
    row.style.setProperty('--vc-tint', `hsl(32 ${6 + (step % 3) * 2}% ${5.5 + step * 0.45}%)`);
  }
}

/* ---------------------------------------------------------- calling points */

/** The same rules as board.js's stopTime, so the boards and the line agree. */
function stopTime(point) {
  if (point.cancelled) return null;
  if (point.actual_time) return { text: point.actual_time, late: false };
  if (point.expected_time && /^\d{2}:\d{2}$/.test(point.expected_time)) {
    return { text: point.expected_time, late: point.scheduled_time != null && point.expected_time > point.scheduled_time };
  }
  return point.scheduled_time ? { text: point.scheduled_time, late: false } : null;
}

/**
 * board.js hands us the block's list and calls again every few seconds with
 * the same stops, so a call that changes nothing only re-measures.
 */
export function renderCallingPoints(list, points, rawPoints, service, showTimes) {
  const names = (rawPoints?.length ? rawPoints : points.map((name) => ({ name })));
  const data = names.map((p) => ({
    name: p.name,
    cancelled: Boolean(p.cancelled),
    time: showTimes ? stopTime(p) : null,
  }));
  const key = JSON.stringify(data);
  let state = stops.get(list);
  if (!state || state.key !== key) {
    state = { key, page: state && state.key === key ? state.page : 0, pages: [[0, data.length]], measured: '' };
    stops.set(list, state);
    build(list, data);
  }
  state.pass = pass;
  stopLists.add(list);
  const wrap = list.closest('.calling-points');
  if (wrap) observer?.observe(wrap);
  layout(list);
}

function build(list, data) {
  const frag = document.createDocumentFragment();
  const wrap = document.createElement('div');
  wrap.className = 'vc-stops';
  for (const stop of data) {
    const board = document.createElement('span');
    board.className = 'vc-stop';
    board.style.setProperty('--vc-tint', `hsl(32 ${6 + (hash(stop.name) % 3) * 2}% ${5.5 + (hash(stop.name) % TINT_STEPS) * 0.45}%)`);
    if (stop.cancelled) board.dataset.cancelled = '';
    const name = document.createElement('span');
    name.className = 'vc-stop-name';
    name.textContent = stop.name;
    board.append(name);
    if (stop.time) {
      const time = document.createElement('span');
      time.className = 'vc-stop-time';
      time.textContent = stop.time.text;
      if (stop.time.late) time.dataset.late = '';
      board.append(time);
    }
    frag.append(board);
  }
  wrap.append(frag);
  list.replaceChildren(wrap);
}

/**
 * Work out which whole boards go on which page and show the current one.
 *
 * Every board is shown first and its position read: boards are in one flex
 * wrap, so the distinct tops are the lines (offsetTop does not depend on the
 * holder's height), and a page is as many lines as the track is tall. Six
 * slots of block normally hold every stop, so there is usually one page. Whole boards only, and page breaks fall on line breaks,
 * so hiding the earlier pages never changes how the later ones wrap.
 */
function layout(list) {
  const state = stops.get(list);
  const wrap = list.closest('.calling-points');
  const holder = list.firstElementChild;
  const track = wrap?.querySelector('.calling-points-track');
  if (!state || !holder || !wrap || !track || wrap.hidden) return;
  const boards = Array.from(holder.children);
  if (!boards.length) return;
  // The room is the track's, which has an explicit height; the holder's own
  // height follows --vc-lines and must not feed back into the measurement.
  const measured = [track.clientWidth, track.clientHeight, document.fonts?.status].join('|');
  if (state.measured !== measured) {
    for (const board of boards) board.hidden = false;
    // The boards are lettered as large as will get every stop on at once:
    // a short route is read from across the room, and a long one drops a size
    // at a time before it is ever paged.
    let lines = [];
    let perPage = 1;
    for (const scale of STOP_SCALES) {
      wrap.style.setProperty('--vc-stop-scale', String(scale));
      const first = boards[0];
      const margin = parseFloat(getComputedStyle(first).marginTop) || 0;
      const pitch = first.offsetHeight + margin;
      perPage = Math.max(1, Math.floor(track.clientHeight / pitch + 0.05));
      lines = [];
      boards.forEach((board, index) => {
        const top = board.offsetTop;
        if (!lines.length || top - lines[lines.length - 1].top > 1) lines.push({ top, from: index });
      });
      if (lines.length <= perPage) break;
    }
    const pages = [];
    for (let i = 0; i < lines.length; i += perPage) {
      pages.push([lines[i].from, i + perPage < lines.length ? lines[i + perPage].from : boards.length]);
    }
    state.pages = pages.length ? pages : [[0, boards.length]];
    // The holder is as tall as the busiest page, so it does not change height
    // as the pages turn.
    let most = 1;
    for (const [from, to] of state.pages) {
      const count = lines.filter((line) => line.from >= from && line.from < to).length;
      if (count > most) most = count;
    }
    if (holder.style.getPropertyValue('--vc-lines') !== String(most)) holder.style.setProperty('--vc-lines', String(most));
    state.measured = measured;
  }
  state.page = Math.min(state.page, state.pages.length - 1);
  show(boards, state);
}

function show(boards, state) {
  const [from, to] = state.pages[state.page];
  boards.forEach((board, index) => {
    const hide = index < from || index >= to;
    if (board.hidden !== hide) board.hidden = hide;
  });
}

function turnPages() {
  for (const list of stopLists) {
    const state = stops.get(list);
    if (!list.isConnected || !state || state.pages.length < 2) continue;
    state.page = (state.page + 1) % state.pages.length;
    const holder = list.firstElementChild;
    if (holder) show(Array.from(holder.children), state);
  }
}

/* -------------------------------------------------------- station and rails */

/**
 * Shrinks the station name just enough to fit its board. The name is painted in
 * heavy capitals at a fixed height, and "London St Pancras International" does
 * not fit half a screen at that size; wrapping would cost a second line of
 * height the rows need, so it is scaled instead, never below a floor where it
 * would stop being a headline. --vc-fit is a multiplier read by victorian.css.
 * Measured again only when the name, the width or the font changes.
 */
function fitStationName(board) {
  const name = board.querySelector('.station-name');
  if (!name) return;
  const key = `${name.textContent}|${name.clientWidth}|${document.fonts?.status}`;
  if (name.dataset.vcFit === key) return;
  name.style.setProperty('--vc-fit', '1');
  const room = name.clientWidth;
  const need = name.scrollWidth;
  if (room > 0 && need > room) {
    name.style.setProperty('--vc-fit', String(Math.max(0.3, (room / need) * 0.97)));
  }
  name.dataset.vcFit = key;
}

export function afterRender(boardsEl) {
  for (const board of boardsEl.querySelectorAll('.board')) {
    const rows = board.querySelector('.rows');
    if (!rows) continue;
    // The header rail reads the mode from an attribute (a pseudo-element
    // cannot read another element), and the mode is board.js's own text.
    const mode = board.querySelector('.board-mode')?.textContent || '';
    if (rows.dataset.vcMode !== mode) rows.dataset.vcMode = mode;

    // Show three trains and six slots of stops while board.js is showing
    // calling points. board.js has already set --calling-share (and raised it
    // on both halves of a split screen) by the time this runs.
    const stopsOn = parseFloat(getComputedStyle(rows).getPropertyValue('--calling-share')) > 0 ? '1' : '0';
    if (rows.dataset.vcStops !== stopsOn) rows.dataset.vcStops = stopsOn;

    // Column headings, first child of .rows. The cells carry the same classes
    // as a service row's so they stand over the same tracks. board.js leaves
    // foreign children of .rows alone.
    let head = rows.querySelector(':scope > .vc-columns');
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
    const name = board.querySelector('.station-name');
    if (name) nameObserver?.observe(name);
  }

  // board.js does not say when the top train has no stops: it empties the list
  // and hides the block, and on a split screen then shows the block again,
  // blank, to keep the two halves level. Boards remembered from the train
  // before would be put back into that blank by the observer, so clear any
  // list this pass did not ask for.
  for (const list of stopLists) {
    if (list.isConnected && stops.get(list)?.pass === pass) continue;
    stopLists.delete(list);
    stops.delete(list);
    if (list.isConnected) list.textContent = '';
  }
  pass += 1;
}
