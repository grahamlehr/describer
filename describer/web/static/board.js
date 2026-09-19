/**
 * Board renderer.
 *
 * Receives whole-state snapshots over SSE and renders them into the shared
 * DOM. Themes never see the API: they get text through `renderText` and a
 * post-render hook, so adding a theme is a CSS file plus an optional module.
 */

const boardsEl = document.getElementById('boards');
const asleepEl = document.getElementById('asleep');
const setupEl = document.getElementById('setup');
const connectionEl = document.getElementById('connection');
const themeLink = document.getElementById('theme-css');
const boardTemplate = document.getElementById('board-template');
const rowTemplate = document.getElementById('row-template');
const weatherHourTemplate = document.getElementById('weather-hour-template');

const MODE_LABELS = { departures: 'Departures', arrivals: 'Arrivals' };
const SOURCE_LABELS = { rdm: 'Darwin', rtt: 'RTT' };
/** Steps through the 24-hour forecast; 8 gives one every three hours. */
const WEATHER_STEP = 3;
const WEATHER_COLUMNS = 24 / WEATHER_STEP;
/** A rain chance below this is not worth a reader's attention. */
const WEATHER_PRECIP_THRESHOLD = 20;
/** How long one page of calling points holds before the next. */
const CALLING_PAGE_MS = 5000;
const CALLING_SEPARATOR = ' \u2022 ';
/**
 * Both feeds word a reason as a sentence written to follow the status, as the
 * announcements do: "This is due to a shortage of train crew". Stripping the
 * lead-in lets it join on to what the train is doing rather than repeating it.
 */
const REASON_LEAD = /^this\s+(?:is|was)\s+/i;
const REASON_JOINS = /^(?:due to|because of|owing to)\b/i;
/** Coach loading bands. Judged by eye, not derived from anything Darwin sends. */
const QUIET_BELOW = 35;
const BUSY_FROM = 70;
/** Pages of stops for each list that board.js paints itself; themes keep their own. */
const callingPages = new WeakMap();
/** Pages of the reason line, for the themes whose text is ordinary type. */
const reasonPages = new WeakMap();

let state = null;
let theme = null;
let themeName = null;
/** The import of the current theme's module, so concurrent passes wait for it. */
let themeLoading = null;
/** Handed to themes so one can repaint on its own schedule. */
const themeApi = { render: () => { render(); } };
/** Clock offset so the board follows the Pi's clock, not the browser's. */
let clockOffsetMs = 0;

const SETUP_HEADINGS = {
  no_key: 'Set up your departure board',
  key_rejected: 'Your rail data key was not accepted',
};

/**
 * The first-run screen. `setup` is null when the board needs nothing, which is
 * nearly always; otherwise it carries why, and where a phone finds /setup. The
 * QR code is the address by number when there is one, since that opens on every
 * phone, and the server picks. The boards stay laid out behind it, so a theme
 * keeps measuring; the screen is opaque.
 */
function renderSetup(setup) {
  setupEl.hidden = !setup;
  if (!setup) return;
  const bare = (url) => url.replace(/^https?:\/\//, '');
  setupEl.querySelector('.setup-heading').textContent =
    SETUP_HEADINGS[setup.reason] || SETUP_HEADINGS.no_key;
  setupEl.querySelector('.setup-url').textContent = bare(setup.url);
  const ip = setupEl.querySelector('.setup-ip');
  ip.hidden = !setup.ip_url;
  ip.textContent = setup.ip_url ? `or ${bare(setup.ip_url)}` : '';
  // The address is in the query so a changed address is a new URL, not a cached one.
  const code = setupEl.querySelector('.setup-qr-code');
  const src = `url("/api/setup/qr.svg?u=${encodeURIComponent(setup.ip_url || setup.url)}")`;
  if (code.dataset.src !== src) {
    code.dataset.src = src;
    code.style.maskImage = src;
  }
}

/* ---------------------------------------------------------------- theming */

async function applyTheme(name, options) {
  if (name === themeName) {
    // A pass that lands while the module is still on its way (the stylesheet's
    // load event fires one) must not paint plain text the theme will never be
    // asked to redo: setText skips cells whose text has not changed.
    await themeLoading;
    theme?.configure?.(options);
    return;
  }
  theme?.detach?.();
  theme = null;
  themeName = name;
  document.body.dataset.theme = name;
  themeLink.href = `/static/themes/${name}.css`;

  // Cells rendered by the previous theme must go back to plain text.
  for (const cell of boardsEl.querySelectorAll('.cell, .service-position')) {
    cell.textContent = cell.dataset.text || '';
    delete cell.dataset.rendered;
  }
  for (const list of boardsEl.querySelectorAll('.calling-points-list')) {
    list.textContent = '';
    callingPages.delete(list);
  }
  // Flaps and canvases belong to the theme that made them; the next theme
  // rebuilds the line from dataset.text on its first pass.
  for (const reason of boardsEl.querySelectorAll('.service-reason')) {
    reason.textContent = '';
    delete reason.dataset.text;
  }
  for (const formation of boardsEl.querySelectorAll('.service-formation')) {
    formation.textContent = '';
    delete formation.dataset.key;
  }

  themeLoading = import(`/static/themes/${name}.js`)
    .then((module) => {
      theme = module;
      theme.attach?.(boardsEl, options, themeApi);
    })
    .catch((err) => {
      // A theme with no JS module is normal (modern); anything else is a bug.
      if (!String(err).includes('Failed to fetch dynamically imported module')) {
        console.error('Theme module failed', err);
      }
    });
  await themeLoading;
}

function setText(el, text) {
  const value = text ?? '';
  if (el.dataset.text === value && el.dataset.rendered === '1') return;
  el.dataset.text = value;
  el.dataset.rendered = '1';
  if (theme?.renderText) theme.renderText(el, value);
  else el.textContent = value;
}

/* --------------------------------------------------------------- rendering */

function ensureBoards(count) {
  boardsEl.dataset.count = String(count);
  while (boardsEl.children.length > count) boardsEl.lastElementChild.remove();
  while (boardsEl.children.length < count) {
    boardsEl.append(boardTemplate.content.cloneNode(true));
  }
  return Array.from(boardsEl.children);
}

function rowFor(rowsEl, service) {
  let row = rowsEl.querySelector(`[data-id="${CSS.escape(service.id)}"]`);
  if (!row) {
    row = rowTemplate.content.firstElementChild.cloneNode(true);
    row.dataset.id = service.id;
  }
  return row;
}

function renderRows(boardEl, board, station) {
  const rowsEl = boardEl.querySelector('.rows');
  // The height is shared between the configured slots, not the ones in use, so
  // the type does not resize every time a train drops off the board.
  rowsEl.style.setProperty('--rows', String(station.rows));
  const services = board.services.slice(0, station.rows);
  const seen = new Set();

  services.forEach((service, index) => {
    const row = rowFor(rowsEl, service);
    seen.add(service.id);
    row.dataset.status = service.status;
    row.dataset.index = String(index);
    row.classList.toggle('selected', index === 0);

    // In the tree before its cells are painted: a detached element has no
    // computed style, and splitflap sizes each field from the CSS --chars it
    // reads there. Painting first gave every cell a grid as wide as its own
    // text, so nothing was ever abbreviated or padded. append() on a row that
    // is already here re-orders it in place, so this stays the ordering step.
    rowsEl.append(row);

    const place = board.mode === 'arrivals' ? service.origin : service.destination;
    setText(row.querySelector('[data-field="time"]'), service.scheduled_time || '');
    setText(row.querySelector('[data-field="destination"]'), place || '');
    setText(row.querySelector('[data-field="platform"]'), service.platform || '-');
    setText(row.querySelector('[data-field="status"]'), statusText(service));
    setText(row.querySelector('[data-field="operator"]'), service.operator_code || '');
  });

  for (const child of Array.from(rowsEl.children)) {
    if (child.classList.contains('row') && !seen.has(child.dataset.id)) child.remove();
    // The placeholder outlives its welcome as soon as there is anything to show.
    if (child.classList.contains('empty') && services.length) child.remove();
  }

  if (!services.length) {
    const empty = rowsEl.querySelector('.empty') || document.createElement('div');
    empty.className = 'empty';
    empty.textContent = board.stale ? 'No data' : 'No services at this time';
    rowsEl.append(empty);
  }
  return services;
}

function statusText(service) {
  // A theme with less room may word this its own way; null means "you decide".
  const custom = theme?.statusText?.(service);
  if (custom != null) return custom;
  switch (service.status) {
    case 'cancelled': return 'Cancelled';
    case 'on_time': return 'On time';
    case 'delayed': return 'Delayed';
    case 'expected': return `Exp ${service.expected_time}`;
    default: return service.expected_time || '';
  }
}

/**
 * Insert `el` right after the last visible one of `candidates`, or after the
 * top row when none are. Keeps the position/formation line, the stops and
 * the reason line in a fixed order under the top service without any one of
 * them needing to know about the others.
 */
function placeAfter(rowsEl, el, ...candidates) {
  let anchor = rowsEl.querySelector('.row');
  for (const candidate of candidates) {
    if (candidate && !candidate.hidden && candidate.parentElement === rowsEl) anchor = candidate;
  }
  if (anchor && el.previousElementSibling !== anchor) {
    rowsEl.insertBefore(el, anchor.nextSibling);
  }
}

/* ------------------------------------------------------------------ detail */

/**
 * "Where is it now", worked out by the parser from the stops it has already
 * left. A theme with less room may word this its own way; null means "you
 * decide". Not shown for a service with no position at all (RTT, or one
 * that starts here or is cancelled).
 */
function positionText(service, mode) {
  const custom = theme?.positionText?.(service, mode);
  if (custom != null) return custom;
  const position = service.position;
  if (!position) return null;
  if (position.state === 'not_started') {
    return position.next ? `Not yet left ${position.next}` : null;
  }
  if (!position.last) return null;
  const when = position.last_time ? ` ${position.last_time}` : '';
  const tail = position.state === 'approaching'
    ? 'next stop here'
    : position.stops_away === 1 ? '1 stop away' : `${position.stops_away} stops away`;
  return `Left ${position.last}${when} · ${tail}`;
}

/** A loading figure as the band a car is coloured by. */
function loadBand(load) {
  if (load == null) return 'unknown';
  return load < QUIET_BELOW ? 'quiet' : load < BUSY_FROM ? 'moderate' : 'busy';
}

/** One car: how busy it is, and inside it "1" and the accessible-toilet sign. */
function buildCarEl(coach, unitStart) {
  const car = document.createElement('span');
  car.className = 'car';
  if (unitStart) car.dataset.unitStart = '';
  const load = coach.loading;
  car.dataset.load = loadBand(load);
  car.style.setProperty('--load', load == null ? '0' : String(Math.min(Math.max(load, 0), 100) / 100));

  const marks = document.createElement('span');
  marks.className = 'car-marks';
  if (coach.first_class) {
    const first = document.createElement('span');
    first.className = 'car-first';
    first.textContent = '1';
    marks.append(first);
  }
  if (coach.accessible_toilet) {
    const toilet = document.createElement('span');
    toilet.className = 'car-wc';
    if (coach.toilet_in_service === false) toilet.dataset.out = '';
    const sign = document.createElement('span');
    sign.className = 'car-wc-sign';
    const text = document.createElement('span');
    text.className = 'car-wc-text';
    text.textContent = 'WC';
    toilet.append(sign, text);
    marks.append(toilet);
  }
  if (marks.childElementCount) car.append(marks);
  return car;
}

/**
 * The coaches as the car-loading panels draw them: one car per coach sharing
 * the width, rounded at the two ends of the train and gapped where one unit
 * couples to the next (the letter in "A4", "B1" changes), each filled to how
 * busy the feed says it is. The coaches arrive front-first — the parser
 * reverses them when Darwin says the formation is — so the front is the left
 * end, and an arrowhead there says so. Inside each car, over the fill: "1"
 * for first class, and the wheelchair sign with "WC" for an accessible
 * toilet, faded and struck through when the feed says it is out of use.
 * Nothing else: standard toilets are noise at this size, and the feed says
 * nothing about wheelchair spaces. A theme may paint the strip its own way
 * instead; none does.
 */
function renderFormation(el, formation, length) {
  if (theme?.renderFormation) {
    theme.renderFormation(el, formation, length);
    return;
  }
  const coaches = formation?.coaches || [];
  const key = JSON.stringify([coaches, length]);
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  el.textContent = '';
  if (!coaches.length) {
    // RTT knows how long a train is and nothing else about it: a row of empty
    // boxes says less than the number does.
    const count = document.createElement('span');
    count.className = 'car-count';
    count.textContent = `${length} ${length === 1 ? 'coach' : 'coaches'}`;
    el.append(count);
    return;
  }
  const cars = document.createElement('span');
  cars.className = 'cars';
  const front = document.createElement('span');
  front.className = 'car-front';
  cars.append(front);
  let unit = null;
  coaches.forEach((coach, index) => {
    const coachUnit = String(coach.number || '').replace(/\d+$/, '');
    cars.append(buildCarEl(coach, index > 0 && coachUnit !== '' && coachUnit !== unit));
    unit = coachUnit;
  });
  el.append(cars);
}

/**
 * Where the top service is, and how it is made up, on one line under its
 * row. Hidden on a stale board — a position from the last good fetch is
 * exactly the thing that must not be shown as live — when both toggles are
 * off, or when there is nothing either would draw.
 */
function renderDetail(boardEl, services, board, display) {
  const wrap = boardEl.querySelector('.service-detail');
  const rowsEl = boardEl.querySelector('.rows');
  const positionEl = wrap.querySelector('.service-position');
  const formationEl = wrap.querySelector('.service-formation');

  // Only a theme that has somewhere to put this line asks for it; every
  // other theme is left exactly as it was.
  if (!theme?.serviceDetail) {
    wrap.hidden = true;
    positionEl.hidden = true;
    formationEl.hidden = true;
    rowsEl.style.setProperty('--detail-share', '0');
    return;
  }

  const top = services[0];
  const live = top && !board.stale;

  const text = live && display.show_position !== false ? positionText(top, board.mode) : null;
  setText(positionEl, text || '');
  positionEl.hidden = !text;

  const hasFormation = Boolean(
    live && display.show_formation !== false && (top.formation || top.length)
  );
  formationEl.hidden = !hasFormation;
  if (hasFormation) {
    renderFormation(formationEl, top.formation, top.length);
  } else {
    formationEl.textContent = '';
    delete formationEl.dataset.key;
  }

  placeAfter(rowsEl, wrap);
  // Where it is and how it is made up are a line each, and each line takes
  // the share the stops' one line does. Only a theme that counts
  // --detail-share in its --slot (modern) feels it.
  const lines = Number(Boolean(text)) + Number(hasFormation);
  rowsEl.style.setProperty('--detail-share', String(0.9 * lines));
  wrap.hidden = !lines;
}

/* ---------------------------------------------------------------- reasons */

/** The reason that goes with the state the train is actually in, or none. */
function reasonFor(service) {
  if (!service) return null;
  if (service.status === 'cancelled') return service.cancel_reason || service.delay_reason || null;
  if (service.status === 'expected' || service.status === 'delayed') return service.delay_reason || null;
  // An on-time train may still carry the reason it was late an hour ago.
  return null;
}

/**
 * "The 15:24 to Oxford is delayed due to a fault with the signalling system."
 *
 * The line names its train, because it does not always sit under the train it
 * is about and because a board is read from across a platform. That is the
 * wording the announcements use, and it is a sentence rather than a column, so
 * every theme pages or scrolls it rather than trusting it to fit.
 */
function reasonText(service, mode) {
  const custom = theme?.reasonText?.(service, mode);
  if (custom != null) return custom;
  const reason = reasonFor(service);
  if (!reason) return null;
  const cancelled = service.status === 'cancelled';
  const place = mode === 'arrivals' ? service.origin : service.destination;
  const train = service.scheduled_time && place
    ? `The ${service.scheduled_time} ${mode === 'arrivals' ? 'from' : 'to'} ${place}`
    : 'This train';
  const lead = `${train} ${cancelled ? 'has been cancelled' : 'is delayed'}`;
  const tail = reason.replace(REASON_LEAD, '').replace(/\.\s*$/, '');
  // Anything we cannot join on to the sentence is printed after a colon, so a
  // reason worded some other way is never mangled into nonsense.
  if (REASON_JOINS.test(tail)) return `${lead} ${tail}`;
  return `${lead}: ${tail.charAt(0).toLowerCase()}${tail.slice(1)}`;
}

/**
 * One line saying why, under the stops of the train it belongs to. The board
 * is describing its top service, so that is whose reason this is; when that
 * train is running normally and a later one is not, the later one's reason
 * takes the line, and the sentence names which train either way.
 */
function renderReason(boardEl, services, mode) {
  const wrap = boardEl.querySelector('.service-reason');
  const rowsEl = boardEl.querySelector('.rows');
  const detailEl = boardEl.querySelector('.service-detail');
  const callingWrap = boardEl.querySelector('.calling-points');
  const top = services[0];
  const service = reasonFor(top) ? top : services.find((candidate) => reasonFor(candidate));
  const text = (service && reasonText(service, mode)) || '';

  // Under the top service, after its position/formation line and its stops
  // when either is showing.
  placeAfter(rowsEl, wrap, detailEl, callingWrap);

  // A board with nothing wrong on it gives the height back to the services.
  rowsEl.style.setProperty('--reason-share', text ? '0.9' : '0');
  wrap.dataset.status = text ? service.status : '';
  wrap.hidden = !text;
  if (!text) {
    wrap.textContent = '';
    delete wrap.dataset.text;
    return;
  }
  wrap.dataset.text = text;
  // A theme with a drum or a matrix rather than type paints this itself, and
  // pages or scrolls it the way that machine would have.
  if (theme?.renderReason) {
    theme.renderReason(wrap, text);
    return;
  }
  paintReasonPages(wrap, text);
}

/**
 * The default renderer. A reason is a sentence, so it will not fit a board
 * line at any size worth reading: pack it into pages that do fit and turn
 * them with the stops above, rather than cutting it off at the margin.
 */
function paintReasonPages(wrap, text) {
  const span = ensureReasonText(wrap);
  const style = getComputedStyle(span);
  // The font is part of the key for the same reason it is for the stops: a
  // theme's stylesheet and web font land after the switch, and pages measured
  // in the old face do not fit the new one.
  const key = [text, style.font, style.letterSpacing, style.textTransform, span.clientWidth].join('|');
  let paged = reasonPages.get(span);
  if (!paged || paged.key !== key) {
    paged = { key, pages: paginateWords(span, text), page: 0 };
    reasonPages.set(span, paged);
  }
  paintPagedText(span, paged);
}

/** The theme owns whatever is in the line; put our own box back when it goes. */
function ensureReasonText(wrap) {
  let span = wrap.querySelector('.service-reason-text');
  if (!span) {
    wrap.textContent = '';
    span = document.createElement('span');
    span.className = 'service-reason-text';
    wrap.append(span);
  }
  return span;
}

/**
 * Pack words into pages that fit the line, never splitting one. Measured by
 * painting candidates into the box itself, so the theme's font, its
 * letter-spacing, its capitals and anything else sharing the line all count.
 */
function paginateWords(span, text) {
  const words = String(text).split(/\s+/).filter(Boolean);
  if (!span.clientWidth || !words.length) return [text];
  const fits = (candidate) => {
    span.textContent = candidate;
    return span.scrollWidth <= span.clientWidth;
  };
  const pages = [];
  let line = '';
  for (const word of words) {
    const joined = line ? `${line} ${word}` : word;
    if (fits(joined)) {
      line = joined;
      continue;
    }
    if (line) pages.push(line);
    // A single word wider than the line is clipped by the box; nothing to do.
    line = word;
  }
  if (line) pages.push(line);
  return pages;
}

/**
 * The time to print beside a calling point, Darwin's own "next train" style,
 * and whether it reads late. `actual_time` (already left) wins when there is
 * one; otherwise an "HH:MM" `expected_time` is used as-is, "On time" and
 * "Delayed" (or no estimate at all) fall back to the booked time, and a
 * cancelled stop prints no time. RTT's `expected_time` is always "HH:MM" or
 * null, so only the "HH:MM" and fallback branches ever fire for it.
 */
function stopTime(point) {
  if (point.cancelled) return null;
  if (point.actual_time) return { text: point.actual_time, late: false };
  if (point.expected_time && /^\d{2}:\d{2}$/.test(point.expected_time)) {
    return { text: point.expected_time, late: point.scheduled_time != null && point.expected_time > point.scheduled_time };
  }
  return point.scheduled_time ? { text: point.scheduled_time, late: false } : null;
}

function renderCallingPoints(boardEl, services, display) {
  const wrap = boardEl.querySelector('.calling-points');
  const list = wrap.querySelector('.calling-points-list');
  const rowsEl = boardEl.querySelector('.rows');
  const detailEl = boardEl.querySelector('.service-detail');
  const first = services[0];
  const rawPoints = display.show_calling_points && first ? first.calling_points : [];
  const points = rawPoints.map((p) => {
    if (!display.show_calling_times) return p.name;
    const time = stopTime(p);
    return time ? `${p.name} (${time.text})` : p.name;
  });

  // An arrival has already made its stops; a departure has them ahead of it.
  // A theme drawing something else in this block may name it its own way.
  const label = theme?.callingPointsLabel?.(boardEl.dataset.mode, first)
    ?? (boardEl.dataset.mode === 'arrivals' ? 'Called at' : 'Calling at');
  const labelEl = wrap.querySelector('.calling-points-label');
  if (labelEl.textContent !== label) labelEl.textContent = label;

  // These stops belong to the top service, so they read directly under it —
  // after its position/formation line when that is showing, else the row.
  placeAfter(rowsEl, wrap, detailEl);

  // A hidden block claims no share of the height; the rows take it instead.
  // 0.9 of a row is one line of label and stops at 0.9 of the row's type with
  // a little air; judged by eye, and it must match the default in base.css.
  rowsEl.style.setProperty('--calling-share', points.length ? '0.9' : '0');

  if (!points.length) {
    wrap.hidden = true;
    list.textContent = '';
    callingPages.delete(list);
    return;
  }
  wrap.hidden = false;

  // A theme may paint the stops its own way; splitflap builds them from flaps.
  // The full point objects are the third argument and the service the fourth;
  // only thameslink uses them, to mark the stops an arrival has already passed
  // and to draw the whole journey in place of the stops.
  if (theme?.renderCallingPoints) {
    callingPages.delete(list);
    theme.renderCallingPoints(list, points, rawPoints, first, display.show_calling_times);
    return;
  }

  // Otherwise pack them into pages that fit the line, and turn a page every
  // few seconds. Repaginate only when the stops or the room for them change.
  // The font is part of the key: a theme's stylesheet and web font land after
  // the switch, and pages measured in the old face do not fit the new one.
  const width = wrap.querySelector('.calling-points-track').clientWidth;
  const style = getComputedStyle(list);
  const key = [points.join(CALLING_SEPARATOR), style.font, style.letterSpacing, style.textTransform].join('|');
  let paged = callingPages.get(list);
  if (!paged || paged.key !== key || paged.width !== width) {
    paged = { key, width, pages: paginateCallingPoints(list, points, width), page: 0 };
    callingPages.set(list, paged);
  }
  paintPagedText(list, paged);
}

/**
 * Pack stops into lines that fit `width`, never splitting a name across pages.
 * Measured by painting candidates into the list itself, so the theme's font,
 * letter-spacing and text-transform all count. Runs only when the stops change.
 */
function paginateCallingPoints(list, points, width) {
  if (!width) return [points.join(CALLING_SEPARATOR)];
  const fits = (text) => {
    list.textContent = text;
    return list.getBoundingClientRect().width <= width;
  };
  const pages = [];
  let line = '';
  for (const point of points) {
    const joined = line ? line + CALLING_SEPARATOR + point : point;
    if (fits(joined)) {
      line = joined;
      continue;
    }
    if (line) pages.push(line);
    // A single name wider than the line is clipped by the track; nothing to do.
    line = point;
  }
  if (line) pages.push(line);
  return pages;
}

function paintPagedText(el, paged) {
  const text = paged.pages[paged.page % paged.pages.length] || '';
  if (el.textContent !== text) el.textContent = text;
}

/** The stops and the reason turn together, so the board reads as one thing. */
function turnPages() {
  for (const list of boardsEl.querySelectorAll('.calling-points-list')) {
    turnOne(list, callingPages);
  }
  for (const span of boardsEl.querySelectorAll('.service-reason-text')) {
    turnOne(span, reasonPages);
  }
}

function turnOne(el, store) {
  const paged = store.get(el);
  if (!paged || paged.pages.length < 2) return;
  paged.page = (paged.page + 1) % paged.pages.length;
  paintPagedText(el, paged);
}
setInterval(turnPages, CALLING_PAGE_MS);

/* ----------------------------------------------------------------- weather */

/** The condition/day-night attributes a `.wx-icon` mask is selected by. */
function fillWeatherIcon(icon, hour) {
  icon.dataset.condition = hour?.condition || 'unknown';
  icon.dataset.day = hour && hour.is_day === false ? '0' : '1';
}

function buildWeatherNow(current) {
  const now = document.createElement('div');
  now.className = 'wx-now';
  const icon = document.createElement('span');
  icon.className = 'wx-icon';
  fillWeatherIcon(icon, current);
  const temp = document.createElement('span');
  temp.className = 'wx-temp';
  temp.textContent = current ? `${Math.round(current.temperature)}°` : '';
  temp.toggleAttribute('data-freezing', Boolean(current) && current.temperature <= 0);
  now.append(icon, temp);
  return now;
}

/** Eight cells at three-hour steps: 24 columns do not fit half a 1280 screen
 *  at a readable size, and a reader wants "roughly when", not the hour. */
function buildWeatherHours(hours) {
  const list = document.createElement('div');
  list.className = 'wx-hours';
  for (let i = 0; i < WEATHER_COLUMNS; i += 1) {
    const hour = hours[i * WEATHER_STEP] || null;
    const cell = weatherHourTemplate.content.firstElementChild.cloneNode(true);
    cell.querySelector('.wx-time').textContent = hour ? hour.time : '';
    fillWeatherIcon(cell.querySelector('.wx-icon'), hour);
    cell.querySelector('.wx-temp').textContent = hour ? `${Math.round(hour.temperature)}°` : '';
    const precip = cell.querySelector('.wx-precip');
    const chance = hour?.precip_chance;
    precip.textContent = chance != null && chance >= WEATHER_PRECIP_THRESHOLD ? `${chance}%` : '';
    list.append(cell);
  }
  return list;
}

/**
 * A 24-hour forecast strip at the foot of the board, for whichever theme
 * opts in (`export const weather = true`, exactly as `serviceDetail` does).
 * Every other theme gets it hidden unconditionally, before a forecast is
 * even asked for — the backend does not know which themes draw this any
 * more than it knows which draw the position line.
 *
 * When the option is on globally but this board has nothing yet (no
 * coordinates for the station, or nothing fresh enough), the strip still
 * renders its full shape with every cell blank, so a split screen's two
 * halves claim the same height and thameslink's clock panel holds the same
 * line on both — there being nothing here that belongs to one service, there
 * is nothing to place, only a size to keep equal everywhere it is shown.
 */
function renderWeather(boardEl, forecast, display) {
  const wrap = boardEl.querySelector('.weather');
  const rowsEl = boardEl.querySelector('.rows');

  if (!theme?.weather || !display.show_weather) {
    wrap.hidden = true;
    wrap.textContent = '';
    delete wrap.dataset.key;
    rowsEl.style.setProperty('--weather-share', '0');
    return;
  }

  if (theme.renderWeather) {
    wrap.hidden = false;
    rowsEl.style.setProperty('--weather-share', '1.6');
    theme.renderWeather(wrap, forecast || null);
    return;
  }

  const key = JSON.stringify(forecast || null);
  if (wrap.dataset.key !== key) {
    wrap.dataset.key = key;
    wrap.textContent = '';
    wrap.append(buildWeatherNow(forecast?.current), buildWeatherHours(forecast?.hours || []));
  }
  wrap.hidden = false;
  // ~1.6 slots: a label line's worth of air plus a value line, judged by eye
  // against the strip's own vh-based type (Addendum 15) — the strip lives
  // outside .rows's container-size scope, so this only nudges the other
  // rows' type down a little to leave room, rather than fixing the strip's
  // own height, which is set in base.css.
  rowsEl.style.setProperty('--weather-share', '1.6');
}

function renderBoard(boardEl, board, station, display, forecast) {
  boardEl.dataset.mode = board.mode;
  boardEl.querySelector('.station-name').textContent = board.name;
  boardEl.querySelector('.board-mode').textContent = MODE_LABELS[board.mode] || board.mode;
  boardEl.querySelector('.clock').hidden = !display.clock;

  // Which feed this board came from. Themes may style it or hide it.
  const source = boardEl.querySelector('.source');
  source.hidden = !board.source;
  source.dataset.source = board.source || '';
  source.textContent = SOURCE_LABELS[board.source] || board.source || '';

  const stale = boardEl.querySelector('.stale');
  stale.hidden = !board.stale;
  stale.textContent = board.stale ? `Data stale${board.error ? ` — ${board.error}` : ''}` : '';

  const services = renderRows(boardEl, board, station);
  renderDetail(boardEl, services, board, display);
  renderCallingPoints(boardEl, services, display);
  renderReason(boardEl, services, board.mode);
  renderWeather(boardEl, forecast, display);

  const messages = boardEl.querySelector('.messages');
  messages.hidden = board.messages.length === 0;
  messages.textContent = board.messages.join('  •  ');
}

async function render() {
  if (!state) return;
  const options = state.display.themes?.[state.display.theme] || {};
  await applyTheme(state.display.theme, options);

  asleepEl.hidden = state.display_on;
  boardsEl.hidden = !state.display_on || !!state.setup;
  renderSetup(state.setup);

  const boardEls = ensureBoards(state.boards.length);
  state.boards.forEach((board, index) => {
    renderBoard(boardEls[index], board, state.stations[index], state.display, state.weather?.[index]);
  });
  syncRowShares(boardEls);
  theme?.afterRender?.(boardsEl);
}

/**
 * A split screen's two boards must end up with the same row height, or the
 * highlighted "next train" row comes out a different size on each half
 * depending only on which of the two happens to have a formation, a delay
 * reason, or more calling points than the other. Give every board the
 * largest share any of them needs for each of the three optional lines, and
 * take the corresponding block out of `hidden` wherever that pushed its
 * share up, so the extra room actually reserves blank space rather than
 * shrinking every row to make space for nothing. `.calling-points-label`
 * would otherwise show "Calling at" on its own with no stops after it, so it
 * is blanked along with the rest; reset first, so a board that later gets
 * real stops of its own is never left with a stray label hidden.
 */
const SHARED_ROW_LINES = [
  { prop: '--detail-share', block: '.service-detail' },
  { prop: '--reason-share', block: '.service-reason' },
  { prop: '--calling-share', block: '.calling-points', alsoHide: ['.calling-points-label'] },
];

function syncRowShares(boardEls) {
  if (boardEls.length < 2) return;
  const rowsEls = boardEls.map((el) => el.querySelector('.rows'));
  for (const { prop, block, alsoHide } of SHARED_ROW_LINES) {
    for (const sel of alsoHide || []) {
      for (const boardEl of boardEls) {
        const el = boardEl.querySelector(sel);
        if (el) el.hidden = false;
      }
    }
    const shares = rowsEls.map((el) => Number(getComputedStyle(el).getPropertyValue(prop)) || 0);
    const max = Math.max(...shares);
    if (max <= 0) continue;
    rowsEls.forEach((rowsEl, i) => {
      rowsEl.style.setProperty(prop, String(max));
      if (shares[i] >= max) return;
      const boardEl = boardEls[i];
      const blockEl = boardEl.querySelector(block);
      if (blockEl) blockEl.hidden = false;
      for (const sel of alsoHide || []) {
        const el = boardEl.querySelector(sel);
        if (el) el.hidden = true;
      }
    });
  }
}

/* ------------------------------------------------------------------ clock */

function tickClock() {
  const now = new Date(Date.now() + clockOffsetMs);
  const text = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((n) => String(n).padStart(2, '0'))
    .join(':');
  for (const clock of boardsEl.querySelectorAll('.clock')) {
    if (clock.textContent !== text) clock.textContent = text;
  }
}

/* -------------------------------------------------------------------- SSE */

/** The release this page was loaded from, as the first state frame named it. */
let runningVersion = null;

function connect() {
  const source = new EventSource('/api/stream');

  source.addEventListener('open', () => { connectionEl.hidden = true; });

  source.addEventListener('message', (event) => {
    connectionEl.hidden = true;
    try {
      state = JSON.parse(event.data);
    } catch (err) {
      console.error('Bad state frame', err);
      return;
    }
    // An update from /admin restarts the backend, and the stream reconnects to
    // a newer release than the files this page is running. The kiosk is never
    // refreshed by hand, so reload here; no-cache makes it fetch the new ones.
    if (state.version && runningVersion && state.version !== runningVersion) {
      location.reload();
      return;
    }
    runningVersion = state.version ?? runningVersion;
    clockOffsetMs = new Date(state.server_time).getTime() - Date.now();
    render();
  });

  source.addEventListener('error', () => {
    // EventSource reconnects on its own; just tell the viewer what is going on.
    connectionEl.hidden = false;
  });
}

connect();
setInterval(tickClock, 250);
tickClock();
// Anything measured before a theme's stylesheet or web font arrived is wrong;
// repaint once they have, and the calling points repaginate in the new face.
themeLink.addEventListener('load', () => { render(); });
document.fonts?.addEventListener('loadingdone', () => { render(); });
