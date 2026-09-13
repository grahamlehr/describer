/**
 * thameslink theme: the LCD panels on the Thameslink core.
 *
 * The top service is given a block of its own with its route drawn under it,
 * and everything else becomes a "Later trains" list under a blue bar. Three
 * things need JavaScript: the ordinal labels ("1st train", "3rd"), the route
 * list, which pages (or, with `scroll_route`, scrolls) down the page rather
 * than along a line, and the countdown in the status column, which has to be
 * recomputed as the clock moves.
 */

import { applyColours, clearColours } from './colours.js';

/** The roles this board has. It counts down in --fg, so it has no on-time
 *  colour to offer; its hairline stays structural, but the "Later trains"
 *  header bar (--tl-bar) is configurable as `bar`. */
const ROLES = ['background', 'text', 'dim_text', 'accent', 'late', 'cancelled', 'bar'];

/** The line sits between the featured train and its route. */
export const serviceDetail = true;

/** How often the countdowns are recomputed, and the delay wording alternates. */
const REFRESH_MS = 15000;
/** How long one page of the route holds before the next. */
const PAGE_MS = 5000;
/** Scrolling, how long the route rests at the top and at the foot. */
const SCROLL_HOLD_MS = 3000;
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
/** display.themes.thameslink.full_journey: the route runs origin to destination. */
let fullJourney = false;
/** display.themes.thameslink.scroll_route, and its two speeds in stops a second:
 *  down the route while it is read, and back up to the top. */
let scrollRoute = false;
let scrollSpeed = 0.5;
let returnSpeed = 4;
export function attach(boardsEl, options, themeApi) {
  root = boardsEl;
  api = themeApi;
  configure(options);
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
      paint(list);
    }
  });
  // board.js writes .clock itself every quarter second and would overwrite any
  // structure we put in it, so the panel is a separate element kept in step.
  clockTimer = setInterval(paintClocks, CLOCK_MS);
}

export function configure(options = {}) {
  applyColours(options.colours, ROLES);
  fullJourney = Boolean(options.full_journey);
  const scroll = [Boolean(options.scroll_route), Number(options.scroll_speed) || 0.5, Number(options.return_speed) || 4];
  // board.js calls this on every pass, so only a real change restarts a route.
  if (scroll.join() === [scrollRoute, scrollSpeed, returnSpeed].join()) return;
  [scrollRoute, scrollSpeed, returnSpeed] = scroll;
  for (const list of callingLists) {
    stopScroll(list);
    paint(list);
  }
}

export function detach() {
  clearColours(ROLES);
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
    stopScroll(list);
    list.style.removeProperty('transform');
    list.parentElement?.style.removeProperty('height');
    delete list.__key;
    delete list.__anchor;
    delete list.__turned;
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
 *
 * On a departures board every stop is ahead of the train, so the dot sits on
 * the last one drawn, as it always has. On an arrivals board the route is
 * the stops *behind* the train, and `rawPoints` now carries an `actual_time`
 * for the ones it has already left: those are dimmed with `.tl-passed`, and
 * the dot moves to the first one it has not — or, once it has left them all,
 * to the last, meaning "arriving next".
 *
 * With `full_journey` on, the route is the service's whole `journey` instead
 * (see `journeyStops`), in either mode, and it opens on the page the train is
 * on rather than at the origin.
 *
 * With `scroll_route` on, the column glides instead of turning (see
 * `paintScroll`): down at one speed, back to the top at another.
 */
export function renderCallingPoints(list, points, rawPoints = [], service = null) {
  const arrivals = list.closest('.board')?.dataset.mode === 'arrivals';
  const journey = journeyFor(service);
  const stops = journey ? journeyStops(journey) : routeStops(points, rawPoints, arrivals);
  // The key carries the classes as well as the names: a stop being left
  // changes nothing but a class, and it must still repaint.
  const key = JSON.stringify(stops);
  if (list.__key !== key) {
    list.__key = key;
    list.textContent = '';
    for (const stop of stops) {
      const el = document.createElement('span');
      el.className = ['tl-stop', ...stop.classes].join(' ');
      el.textContent = stop.name;
      if (stop.train) {
        const marker = document.createElement('i');
        marker.className = 'tl-train';
        el.append(marker);
      }
      list.append(el);
    }
    const train = stops.findIndex((stop) => stop.train);
    const here = stops.findIndex((stop) => stop.classes.includes('tl-here'));
    list.__anchor = train !== -1 ? train : Math.max(0, here);
    list.__page = 0;
    list.__turned = false;
    // A new route scrolls from where the train is, not from where the last
    // one had got to.
    stopScroll(list);
  }
  callingLists.add(list);
  // Observing the block, not the track: the track's height is ours to set, and
  // the block's is what the board actually handed the route.
  if (list.parentElement) observer?.observe(list.parentElement.parentElement);
  measure(list);
  paint(list);
}

/** The top service's whole run, when it is wanted and the feed gave us one. */
function journeyFor(service) {
  return fullJourney && service?.journey?.length ? service.journey : null;
}

/** "Journey" names the block when it holds the whole run; null keeps the default. */
export function callingPointsLabel(mode, service) {
  return journeyFor(service) ? 'Journey' : null;
}

/** The stops still to come, or on an arrival the ones behind it. */
function routeStops(points, rawPoints, arrivals) {
  const firstUnreached = arrivals ? rawPoints.findIndex((point) => !point.actual_time) : -1;
  const finalIndex = arrivals && firstUnreached !== -1 ? firstUnreached : points.length - 1;
  return points.map((name, index) => ({
    name,
    classes: [
      index === finalIndex && 'tl-final',
      arrivals && index < finalIndex && 'tl-passed',
    ].filter(Boolean),
  }));
}

/**
 * The whole run, origin to destination. Stops the train has left are dimmed,
 * this station is ringed, the destination keeps the filled dot, and an
 * arrowhead on the line sits just above the next stop the train will reach.
 * Only the stops behind this station carry an actual time, which is exactly
 * where the train can be; a train that has not left its origin gets no arrow.
 */
function journeyStops(journey) {
  const here = journey.findIndex((point) => point.here);
  let left = -1;
  journey.forEach((point, index) => {
    if (index < here && point.actual_time) left = index;
  });
  const last = journey.length - 1;
  return journey.map((point, index) => ({
    name: point.name,
    classes: [
      index <= left && 'tl-passed',
      index === here && 'tl-here',
      index === last && index !== here && 'tl-final',
    ].filter(Boolean),
    train: left !== -1 && index === left + 1,
  }));
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

function paint(list) {
  if (scrollRoute) {
    paintScroll(list);
    return;
  }
  stopScroll(list);
  paintPage(list);
}

function paintPage(list) {
  const pages = pageCount(list);
  // Until the first turn, open on the page the train is on (the first page,
  // for anything but a full journey). Worked out again on every paint rather
  // than once, because the room for the stops is still settling while the
  // board loads, and with it how many stops make a page.
  if (!list.__turned && list.__perPage) {
    list.__page = Math.floor((list.__anchor || 0) / list.__perPage);
  }
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
      stopScroll(list);
      callingLists.delete(list);
      continue;
    }
    // Measured every turn, not once: the room for the stops moves with the
    // board's height, the row count and the theme's font arriving late.
    measure(list);
    if (!scrollRoute && pageCount(list) > 1) {
      list.__page = (list.__page || 0) + 1;
      list.__turned = true;
    }
    paint(list);
  }
}

/* ------------------------------------------------- scrolling instead of paging */

/**
 * The column glides down to its last stop at `scrollSpeed`, rests, glides back
 * to the top at `returnSpeed`, rests, and goes again. Each leg is one CSS
 * transition on the transform, so the compositor runs it and the Pi's main
 * thread only wakes at the ends; a timer per list says when a leg is over.
 *
 * `list.__scroll` holds the leg in progress (`phase`: top, down, bottom, up)
 * and the geometry it was planned against. This is called on every render and
 * every re-measure, so it leaves a leg alone unless the room or the stop height
 * has moved, and then carries on in the same direction from wherever the
 * column has got to rather than jumping back to the top.
 */
function paintScroll(list) {
  paintPageLabel(list, 0, 1);
  const stop = list.__stop || 0;
  const max = list.__perPage ? Math.max(0, (list.children.length - list.__perPage) * stop) : 0;
  if (!max) {
    stopScroll(list);
    list.style.transform = 'none';
    return;
  }
  let state = list.__scroll;
  if (state && Math.abs(state.max - max) < 0.5 && Math.abs(state.stop - stop) < 0.05) return;
  const resuming = Boolean(state);
  if (!state) state = list.__scroll = { phase: 'top', timer: null };
  state.max = max;
  state.stop = stop;
  if (!resuming) {
    // Open where the train is, with the stop it last left above it, as paging
    // opens on the train's page; after the first descent, from the top.
    const start = list.__turned ? 0 : Math.max(0, (list.__anchor || 0) - 1) * stop;
    rest(list, Math.min(start, max), 'top');
  } else if (state.phase === 'down') {
    descend(list);
  } else if (state.phase === 'up') {
    ascend(list);
  } else {
    rest(list, state.phase === 'bottom' ? max : Math.min(currentOffset(list), max), state.phase);
  }
}

function rest(list, offset, phase) {
  const state = list.__scroll;
  glide(list, offset, 0);
  state.phase = phase;
  state.timer = setTimeout(() => (phase === 'top' ? descend(list) : ascend(list)), SCROLL_HOLD_MS);
}

function descend(list) {
  const state = list.__scroll;
  const seconds = Math.max(0, state.max - currentOffset(list)) / state.stop / scrollSpeed;
  list.__turned = true;
  glide(list, state.max, seconds, 'linear');
  state.phase = 'down';
  state.timer = setTimeout(() => rest(list, state.max, 'bottom'), seconds * 1000);
}

function ascend(list) {
  const state = list.__scroll;
  const seconds = Math.max(0, currentOffset(list)) / state.stop / returnSpeed;
  glide(list, 0, seconds, 'ease-in-out');
  state.phase = 'up';
  state.timer = setTimeout(() => rest(list, 0, 'top'), seconds * 1000);
}

/** Move the column to `offset` over `seconds`, from wherever it is right now. */
function glide(list, offset, seconds, easing = 'linear') {
  clearTimeout(list.__scroll?.timer);
  // Pin it where a leg in flight has got to, so a change of course starts
  // from there rather than from that leg's destination. Read it before
  // touching the transition: a style read with `transition: none` already set
  // cancels the leg, and what comes back is where it was going.
  const from = currentOffset(list);
  list.style.transition = 'none';
  list.style.transform = `translateY(${-from}px)`;
  if (seconds > 0) {
    void list.offsetHeight;
    list.style.transition = `transform ${seconds}s ${easing}`;
  }
  list.style.transform = `translateY(${-offset}px)`;
}

/** How far the column is scrolled now, a transition in flight included. */
function currentOffset(list) {
  const transform = getComputedStyle(list).transform;
  return transform && transform !== 'none' ? -new DOMMatrixReadOnly(transform).m42 : 0;
}

function stopScroll(list) {
  if (!list.__scroll) return;
  clearTimeout(list.__scroll.timer);
  list.style.removeProperty('transition');
  delete list.__scroll;
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
