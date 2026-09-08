/**
 * thameslink theme: the LCD panels on the Thameslink core.
 *
 * The top service is given a block of its own with its route drawn under it,
 * and everything else becomes a "Later trains" list under a blue bar. Three
 * things need JavaScript: the ordinal labels ("1st train", "3rd"), the route
 * list, which pages down the page rather than along a line, and the countdown
 * in the status column, which has to be recomputed as the clock moves.
 */

/** How often the countdowns are recomputed, and the delay wording alternates. */
const REFRESH_MS = 15000;
/** How long one page of the route holds before the next. */
const PAGE_MS = 5000;
/** How often the clock panel reads back the clock board.js is writing. */
const CLOCK_MS = 500;
/** Beyond this a countdown says less than the time itself does. */
const MAX_COUNTDOWN = 99;
/** The headings over the later trains, by board mode. */
const LATER_HEADINGS = {
  departures: ['Later trains', 'Plat', 'Departs'],
  arrivals: ['Later trains', 'Plat', 'Arrives'],
};

/** Route lists we are paging, so the page timer can find them again. */
const callingLists = new Set();
let refreshTimer = null;
let pageTimer = null;
let clockTimer = null;
/** Re-measures the route the moment the board hands it a different height. */
let observer = null;
let root = null;
let api = null;
/** Shared by every delayed service on screen, so they alternate together. */
let delayPhase = false;

export function attach(boardsEl, _options, themeApi) {
  root = boardsEl;
  api = themeApi;
  refreshTimer = setInterval(() => {
    delayPhase = !delayPhase;
    api.render();
  }, REFRESH_MS);
  pageTimer = setInterval(turnCallingPage, PAGE_MS);
  // The room for the stops is settled by flex, and it is still moving while
  // the theme's stylesheet lands and the later trains take their share. Waiting
  // for the next page turn to notice would leave a name cut in half on screen
  // for five seconds every time the board loads, so watch the block instead.
  observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const list = entry.target.querySelector('.calling-points-list');
      if (!list || !list.__key) continue;
      measure(list);
      paintPage(list);
    }
  });
  // board.js writes .clock itself every quarter second and would overwrite any
  // structure we put in it, so the panel is a separate element kept in step.
  clockTimer = setInterval(paintClocks, CLOCK_MS);
}

export function detach() {
  clearInterval(refreshTimer);
  clearInterval(pageTimer);
  clearInterval(clockTimer);
  refreshTimer = pageTimer = clockTimer = null;
  observer?.disconnect();
  observer = null;
  callingLists.clear();
  for (const el of document.querySelectorAll('.tl-later-head, .tl-pages, .tl-clock, .tl-fill')) {
    el.remove();
  }
  for (const row of document.querySelectorAll('.row[data-ordinal]')) delete row.dataset.ordinal;
  for (const rows of document.querySelectorAll('.rows')) {
    rows.style.removeProperty('--head-share');
    rows.style.removeProperty('--missing');
  }
  for (const list of document.querySelectorAll('.calling-points-list')) {
    list.style.removeProperty('transform');
    list.parentElement?.style.removeProperty('height');
    delete list.__key;
  }
  root = api = null;
}

/* ------------------------------------------------------------------ cells */

/** board.js calls this for every cell instead of setting textContent. */
export function renderText(cell, text) {
  if (cell.dataset.field === 'platform') {
    // "-" is board.js saying there is no platform yet; the panel says nothing.
    cell.textContent = text === '-' ? '' : text;
    return;
  }
  if (cell.dataset.field !== 'destination') {
    cell.textContent = text;
    return;
  }
  // "Sutton via Wimbledon" is two things: where it goes, and how it gets
  // there. The second is what tells two Suttons apart, so it stays, quieter.
  const match = /^(.*?)\s+(via\s+.*)$/i.exec(text);
  cell.textContent = match ? match[1] : text;
  if (!match) return;
  const via = document.createElement('span');
  via.className = 'tl-via';
  via.textContent = match[2];
  cell.append(via);
}

/**
 * These panels count down rather than printing an estimate: "6 min", "Due".
 * A delayed train needs both the countdown and the time it is now expected,
 * and there is one column for them, so they alternate on the refresh tick.
 */
export function statusText(service) {
  if (service.status === 'cancelled') return 'Cancelled';
  if (service.status === 'expected' && delayPhase && service.expected_time) {
    return `Exp ${service.expected_time}`;
  }
  const time = service.status === 'expected' ? service.expected_time : service.scheduled_time;
  const mins = minutesUntil(time);
  if (mins === null) return service.status === 'delayed' ? 'Delayed' : null;
  if (mins > MAX_COUNTDOWN) return time;
  if (mins <= 0) return 'Due';
  return `${mins} min`;
}

/** Now, from the clock board.js keeps against the Pi rather than the browser. */
function nowMinutes() {
  const shown = /^(\d{1,2}):(\d{2})/.exec(document.querySelector('.clock')?.textContent || '');
  if (shown) return Number(shown[1]) * 60 + Number(shown[2]);
  const now = new Date();
  return now.getHours() * 60 + now.getMinutes();
}

/** Minutes from now until "HH:MM", or null for "On time", "Delayed" and the like. */
function minutesUntil(text) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(text || '');
  if (!match) return null;
  let diff = Number(match[1]) * 60 + Number(match[2]) - nowMinutes();
  // Board times carry no date, so a train either side of midnight is nearby.
  if (diff < -720) diff += 1440;
  if (diff > 720) diff -= 1440;
  return diff;
}

/* ---------------------------------------------------------- the route list */

/**
 * board.js hands us the stops for the top service. They are drawn down the
 * page against a line, so a list too long to fit is turned a page at a time
 * by sliding the whole column: the line then runs on across the page turn
 * exactly as it does on the real panels.
 */
export function renderCallingPoints(list, points) {
  const key = points.join('\n');
  if (list.__key !== key) {
    list.__key = key;
    list.textContent = '';
    points.forEach((point, index) => {
      const stop = document.createElement('span');
      stop.className = index === points.length - 1 ? 'tl-stop tl-final' : 'tl-stop';
      stop.textContent = point;
      list.append(stop);
    });
    list.__page = 0;
  }
  callingLists.add(list);
  // Observing the block, not the track: the track's height is ours to set, and
  // the block's is what the board actually handed the route.
  if (list.parentElement) observer?.observe(list.parentElement.parentElement);
  measure(list);
  paintPage(list);
}

/**
 * How many stops fit the track, measured rather than assumed from the CSS.
 * The track is then snapped to that many, because half a station name showing
 * under the fold reads as a fault rather than as a page that continues.
 */
function measure(list) {
  const track = list.parentElement;
  const wrap = track ? track.parentElement : null;
  if (!wrap) return;
  // Our own snap must not be measured back as the room available; the share of
  // the board the stops were given is the flex basis, not the height we set.
  track.style.removeProperty('height');
  // Measured from the block, not from the track: the track is a 1fr grid row
  // inside a flex item, and Chromium sizes that to its content rather than to
  // the share of the board the flex layout actually handed the block.
  const room = wrap.clientHeight - (track.getBoundingClientRect().top - wrap.getBoundingClientRect().top);
  const first = list.firstElementChild;
  const stop = first ? first.getBoundingClientRect().height : 0;
  // Before the stylesheet lands there is nothing to measure; board.js renders
  // again on its load event, and the page timer comes back round regardless.
  if (!stop || !room) {
    list.__perPage = 0;
    return;
  }
  list.__stop = stop;
  // A stop a fraction of a pixel too tall must not cost a whole page.
  list.__perPage = Math.max(1, Math.floor((room + 1) / stop));
  track.style.height = `${Math.min(list.children.length, list.__perPage) * stop}px`;
}

function pageCount(list) {
  if (!list.__perPage) return 1;
  return Math.max(1, Math.ceil(list.children.length / list.__perPage));
}

function paintPage(list) {
  const pages = pageCount(list);
  const page = (list.__page || 0) % pages;
  const offset = list.__perPage ? page * list.__perPage * list.__stop : 0;
  // Sliding the column rather than replacing it keeps the route line running
  // on across the page turn, and keeps the paging off the main thread.
  list.style.transform = offset ? `translateY(${-offset}px)` : 'none';
  paintPageLabel(list, page, pages);
}

/** "Page 2 of 4", in an element of ours: board.js rewrites the label itself. */
function paintPageLabel(list, page, pages) {
  const wrap = list.closest('.calling-points');
  if (!wrap) return;
  let label = wrap.querySelector('.tl-pages');
  if (pages < 2) {
    if (label) label.remove();
    return;
  }
  if (!label) {
    label = document.createElement('div');
    label.className = 'tl-pages';
    wrap.append(label);
  }
  const text = `Page ${page + 1} of ${pages}`;
  if (label.textContent !== text) label.textContent = text;
}

function turnCallingPage() {
  for (const list of callingLists) {
    if (!list.isConnected) {
      callingLists.delete(list);
      continue;
    }
    // Measured every turn, not once: the room for the stops moves with the
    // board's height, the row count and the theme's font arriving late.
    measure(list);
    if (pageCount(list) > 1) list.__page = (list.__page || 0) + 1;
    paintPage(list);
  }
}

/* --------------------------------------------------------------- the clock */

function paintClocks() {
  if (!root) return;
  for (const board of root.querySelectorAll('.board')) {
    const source = board.querySelector('.clock');
    const panel = board.querySelector('.tl-clock');
    if (!source || !panel) continue;
    // display.clock off: board.js hides the original, so the panel goes too.
    panel.hidden = source.hidden;
    const [hhmm, seconds] = splitClock(source.textContent);
    const [time, secs] = panel.children;
    if (time.textContent !== hhmm) time.textContent = hhmm;
    if (secs.textContent !== seconds) secs.textContent = seconds;
  }
}

/** "15:22:01" as the panel prints it: the minute large, the seconds small. */
function splitClock(text) {
  const match = /^(\d{1,2}:\d{2})(?::(\d{2}))?/.exec(text || '');
  if (!match) return ['', ''];
  return [match[1], match[2] ? `.${match[2]}` : ''];
}

/* -------------------------------------------------------- after each pass */

export function afterRender(boardsEl) {
  for (const board of boardsEl.querySelectorAll('.board')) {
    const rows = board.querySelector('.rows');
    const services = Array.from(rows.querySelectorAll('.row'));

    services.forEach((row, index) => {
      // A cancelled service keeps its line but loses its place in the queue.
      row.dataset.ordinal = row.dataset.status === 'cancelled' && index ? '···' : ordinal(index + 1);
    });

    // The stops under the top service are that train's, so they take its
    // status: a cancelled route greys out with the row it belongs to.
    const stops = board.querySelector('.calling-points');
    if (stops) stops.dataset.status = services[0]?.dataset.status || '';

    layOutLaterHead(board, rows, services.length);
    layOutFill(rows, services.length);
    ensureClock(board);
  }
  paintClocks();
}

/** The blue bar over the later trains, and the height it claims. */
function layOutLaterHead(board, rows, count) {
  let head = rows.querySelector('.tl-later-head');
  if (count < 2) {
    // Nothing under the bar, so it claims no height either.
    if (head) head.remove();
    rows.style.setProperty('--head-share', '0');
    return;
  }
  if (!head) {
    head = document.createElement('div');
    head.className = 'tl-later-head';
    for (let i = 0; i < 3; i += 1) head.append(document.createElement('span'));
  }
  rows.style.removeProperty('--head-share');
  const labels = LATER_HEADINGS[board.dataset.mode] || LATER_HEADINGS.departures;
  Array.from(head.children).forEach((span, i) => {
    if (span.textContent !== labels[i]) span.textContent = labels[i];
  });
  // The bar belongs above the second service, and board.js re-appends the rows
  // and moves the calling points on every pass, so put it back each time.
  const second = rows.querySelectorAll('.row')[1];
  if (second && second.previousElementSibling !== head) rows.insertBefore(head, second);
}

/**
 * Reserve the height of the trains a short board does not have. The route is
 * the only thing here that grows, so without this it swallows the surplus and
 * the bar walks down the emptier half of a split screen; with it the bar sits
 * on the same line on both, and the shortfall shows as black under the list.
 */
function layOutFill(rows, count) {
  let fill = rows.querySelector('.tl-fill');
  const configured = Number(getComputedStyle(rows).getPropertyValue('--rows')) || 0;
  // Nothing to line up against when there is no list and no bar.
  const missing = count > 1 ? Math.max(0, configured - count) : 0;
  if (!missing) {
    fill?.remove();
    rows.style.removeProperty('--missing');
    return;
  }
  if (!fill) {
    fill = document.createElement('div');
    fill.className = 'tl-fill';
  }
  rows.style.setProperty('--missing', String(missing));
  // board.js appends the rows on every pass, so the filler goes back last.
  rows.append(fill);
}

function ensureClock(board) {
  if (board.querySelector('.tl-clock')) return;
  const panel = document.createElement('div');
  panel.className = 'tl-clock';
  const time = document.createElement('span');
  const seconds = document.createElement('span');
  seconds.className = 'tl-seconds';
  panel.append(time, seconds);
  board.append(panel);
}

function ordinal(n) {
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] || 'th'}`;
}
