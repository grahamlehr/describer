/**
 * victorian theme: a station train indicator on a brick wall.
 *
 * The stylesheet draws the wall, the frame and the boards. This module has
 * six jobs the shared DOM cannot do on its own:
 *
 *  - the station clock: one mechanical dial however many boards are up, its
 *    seconds hand stepping on the second by the Pi's time;
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
 *    to its board, on two lines when it needs them.
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
/** The dial, its three hands, the angle each is at, and the tick's timer. */
let clockEl = null;
let hands = null;
let clockTimer = null;
/** board.js's clock, corrected to the Pi's; the browser's until it is handed over. */
let now = () => new Date();

export function attach(boardsEl, options, api) {
  if (api?.now) now = api.now;
  buildClock(boardsEl);
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
  // The name's room moves when the clock or the board does; refit then,
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
  clearTimeout(clockTimer);
  clockTimer = null;
  clockEl?.remove();
  clockEl = null;
  hands = null;
  now = () => new Date();
  for (const el of document.querySelectorAll('.boards')) delete el.dataset.vcClock;
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
    name.style.removeProperty('white-space');
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

/* -------------------------------------------------------------------- clock */

const SVG = 'http://www.w3.org/2000/svg';
/** IIII, not IV: the dial painter's four. */
const NUMERALS = ['XII', 'I', 'II', 'III', 'IIII', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI'];

function svg(tag, attrs, parent) {
  const el = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
  parent?.append(el);
  return el;
}

/** One hand: an SVG of its own over the face, turned as a whole by CSS. */
function hand(name, build) {
  const el = svg('svg', { viewBox: '0 0 200 200', class: `vc-hand vc-hand-${name}`, 'aria-hidden': 'true' });
  build(el);
  return el;
}

/**
 * The dial is built once and never repainted: case, bezel, enamel, minute
 * track and numerals in one SVG. The numerals stand on the centre, so those
 * round the foot of the dial are upside down, as on any Roman dial.
 */
function buildClock(boardsEl) {
  clockEl = document.createElement('div');
  clockEl.className = 'vc-clock';
  clockEl.dataset.vcSetting = '';

  const face = svg('svg', { viewBox: '0 0 200 200', 'aria-hidden': 'true' }, clockEl);
  // Turned mahogany case: three flat rings, then the brass bezel.
  svg('circle', { cx: 100, cy: 100, r: 100, fill: '#2a170d' }, face);
  svg('circle', { cx: 100, cy: 100, r: 97, fill: '#5b3a24' }, face);
  svg('circle', { cx: 100, cy: 100, r: 92.5, fill: '#3b2415' }, face);
  svg('circle', { cx: 100, cy: 100, r: 90, fill: '#6a4429' }, face);
  svg('circle', { cx: 100, cy: 100, r: 85.5, fill: '#7d6224' }, face);
  svg('circle', { cx: 100, cy: 100, r: 84.5, fill: '#b8923f' }, face);
  svg('circle', { cx: 100, cy: 100, r: 81.5, fill: '#4a3910' }, face);
  svg('circle', { cx: 100, cy: 100, r: 80.5, fill: '#f6efdc' }, face);
  // Minute track: two fine rings with a stroke a minute, heavier at the fives.
  svg('circle', { cx: 100, cy: 100, r: 76, fill: 'none', stroke: '#1a1612', 'stroke-width': 0.9 }, face);
  svg('circle', { cx: 100, cy: 100, r: 69, fill: 'none', stroke: '#1a1612', 'stroke-width': 0.9 }, face);
  for (let i = 0; i < 60; i += 1) {
    const five = i % 5 === 0;
    svg('line', {
      x1: 100, y1: 24, x2: 100, y2: 31,
      stroke: '#1a1612', 'stroke-width': five ? 3 : 0.9,
      transform: `rotate(${i * 6} 100 100)`,
    }, face);
  }
  NUMERALS.forEach((numeral, i) => {
    const text = svg('text', {
      x: 100, y: 52, 'text-anchor': 'middle', 'font-size': 19,
      // Tall and narrow, as painted numerals are; VIII is the widest.
      textLength: numeral.length > 3 ? 27 : numeral.length * 6.6 + (/[VX]/.test(numeral) ? 5 : 0),
      lengthAdjust: 'spacingAndGlyphs',
      transform: `rotate(${i * 30} 100 100)`,
    }, face);
    text.textContent = numeral;
  });
  svg('circle', { cx: 100, cy: 100, r: 30, fill: 'none', stroke: '#1a1612', 'stroke-width': 0.6 }, face);

  const blued = '#141c2c';
  const hour = hand('hour', (el) => {
    svg('path', { fill: blued, d: 'M97.4 112 L97.9 71 C86 71 85 60 100 46 C115 60 114 71 102.1 71 L102.6 112 Z' }, el);
  });
  const minute = hand('minute', (el) => {
    svg('path', { fill: blued, d: 'M98.1 114 L98.7 50 C92.5 50 92 42 100 25 C108 42 107.5 50 101.3 50 L101.9 114 Z' }, el);
  });
  const second = hand('second', (el) => {
    svg('path', { fill: '#1a1612', d: 'M99.3 100 L99.75 25 L100.25 25 L100.7 100 L101.2 124 L98.8 124 Z' }, el);
    svg('circle', { cx: 100, cy: 118, r: 3.6, fill: '#1a1612' }, el);
    // The brass boss the hands turn on.
    svg('circle', { cx: 100, cy: 100, r: 4.2, fill: '#7d6224' }, el);
    svg('circle', { cx: 100, cy: 100, r: 2.6, fill: '#b8923f' }, el);
  });
  clockEl.append(hour, minute, second);
  hands = { hour: { el: hour, angle: 0 }, minute: { el: minute, angle: 0 }, second: { el: second, angle: 0 } };

  // After .boards, not in it: board.js counts .boards' children as boards.
  boardsEl.after(clockEl);
  tick();
}

/** Turn a hand to an angle by the shorter way round, so it never runs back. */
function turn(part, degrees) {
  const delta = ((((degrees - part.angle) % 360) + 540) % 360) - 180;
  if (Math.abs(delta) < 0.01 && part.el.style.transform) return;
  part.angle += delta;
  part.el.style.transform = `rotate(${part.angle.toFixed(2)}deg)`;
}

/**
 * One beat. The seconds hand steps on the second; the minute hand creeps, a
 * tenth of a degree a second, and the hour hand with it, as a going train
 * moves them. Timed from the clock itself rather than an interval, so the
 * step lands on the second and does not drift.
 */
function tick() {
  if (!clockEl || !hands) return;
  const time = now();
  const seconds = time.getSeconds();
  const minutes = time.getMinutes() + seconds / 60;
  const hours = (time.getHours() % 12) + minutes / 60;
  turn(hands.second, seconds * 6);
  turn(hands.minute, minutes * 6);
  turn(hands.hour, hours * 30);
  if ('vcSetting' in clockEl.dataset) {
    // Placed without a transition; the next beat is the first to swing.
    void clockEl.offsetWidth;
    delete clockEl.dataset.vcSetting;
  }
  clockTimer = setTimeout(tick, 1000 - time.getMilliseconds() + 4);
}

/* -------------------------------------------------------- station and rails */

/** No name is painted smaller than this share of the full size. */
const NAME_FLOOR = 0.4;
/** A name that fits one line at this share of full size or more is not wrapped. */
const ONE_LINE_FLOOR = 0.85;

/**
 * Fits the station name to its board. It is painted in heavy capitals, on one
 * line if a squeeze of no more than 15% gets it there, and otherwise
 * takes a second line ("London St Pancras / International"),
 * as a running-in board did. Only when two lines at full size are still not
 * enough is it scaled down, a step at a time, never below a floor where it
 * would stop being a headline. --vc-fit is a multiplier read by victorian.css.
 * Measured again only when the name, its room or the font changes.
 */
function fitStationName(board) {
  const name = board.querySelector('.station-name');
  const header = board.querySelector('.board-header');
  if (!name || !header) return;
  const key = `${name.textContent}|${header.clientWidth}|${header.clientHeight}|${document.fonts?.status}`;
  if (name.dataset.vcFit === key) return;
  const fits = () => {
    const lineHeight = parseFloat(getComputedStyle(name).lineHeight) || 1;
    // clientHeight, not scrollHeight: Besley's ink overhangs its line box.
    return name.clientHeight <= lineHeight * 2 + 1 && name.scrollWidth <= name.clientWidth + 1;
  };
  let fit = 1;
  name.style.setProperty('--vc-fit', '1');
  // One line is better than two where a small squeeze gets it: measure the
  // name unwrapped first.
  name.style.whiteSpace = 'nowrap';
  const squeeze = name.scrollWidth > name.clientWidth ? (name.clientWidth / name.scrollWidth) * 0.98 : 1;
  if (squeeze >= ONE_LINE_FLOOR) {
    name.style.setProperty('--vc-fit', Math.min(1, squeeze).toFixed(2));
    name.dataset.vcFit = key;
    return;
  }
  name.style.removeProperty('white-space');
  while (name.clientWidth > 0 && !fits() && fit > NAME_FLOOR) {
    fit = Math.max(NAME_FLOOR, fit - 0.05);
    name.style.setProperty('--vc-fit', fit.toFixed(2));
  }
  name.dataset.vcFit = key;
}

export function afterRender(boardsEl) {
  // board.js hides its own clock when display.clock is off; the dial follows,
  // and the nameboards take back the room they left for it.
  const clockOn = boardsEl.querySelector('.clock:not([hidden])') ? '1' : '0';
  if (boardsEl.dataset.vcClock !== clockOn) boardsEl.dataset.vcClock = clockOn;

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
    const header = board.querySelector('.board-header');
    if (header) nameObserver?.observe(header);
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
