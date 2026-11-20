/**
 * dial theme: the next hour on a station clock.
 *
 * Each board carries an analogue dial. A minute hand points at the minute it
 * is now, and every train leaving in the next hour is a marker on the rim at
 * the minute it goes, so the gap between the hand and a marker is how long you
 * have. The middle of the dial says the one thing people look up for: minutes
 * until the next train. The ordinary list sits beside it (or under it) and is
 * not replaced, only counted down in the same words.
 *
 * What needs JavaScript:
 *  - the dial itself, one per board, built lazily in afterRender because
 *    boards come and go as the station count changes between one and two;
 *  - which service each row is (renderRow), so the markers know the trains;
 *  - the countdown wording of the status column, which has to move with the
 *    clock, and the late trains' alternation of "N min" with "Exp HH:MM".
 *
 * "Now" is read back from the `.clock` element, never from the browser's own
 * clock: board.js keeps that against the Pi, and a kiosk whose own clock is
 * a few minutes out must still point at the right minute.
 */

/** The dial redraws, and the hand moves on, this often. */
const TICK_MS = 1000;
/** How often a late train's status flips between "N min" and "Exp HH:MM". */
const REFRESH_MS = 15000;
/** A train this far in the past still sits at its minute: it is leaving. */
const GRACE_MINUTES = 2;
/** The dial shows the coming hour; further away than this gets no marker. */
const HORIZON_MINUTES = 60;
/** The clock has moved a whole day if a "past" train is this far behind. */
const PAST_WINDOW = 30;
const DAY_MINUTES = 24 * 60;

const SVG_NS = 'http://www.w3.org/2000/svg';

/*
 * Geometry, in viewBox units with the centre of the dial at 0,0. The viewBox
 * is a little over 100 either way so the outer ring of markers is not clipped.
 * Everything inside the tick ring is the centre text's, and the CSS sizes that
 * from the same radii: if you move TICK_MAJOR_FROM, check `.dial-centre`.
 */
const EDGE = 107;
const TICK_OUTER = 68;
const TICK_MINOR_FROM = 62;
const TICK_MAJOR_FROM = 57;
const HAND_FROM = 58;
const HAND_TO = 72;
/** Marker centres: the first ring, and the one a second train in a minute takes. */
const RINGS = [80, 97];
const R_NEXT = 9.5;
const R_OTHER = 7;
/** Platform text inside a marker; longer than this and the marker stays plain. */
const PLATFORM_FIT = 2;

let root = null;
let api = null;
let tickTimer = null;
let phaseTimer = null;
/** Shared by every late service on screen, so they flip together. */
let latePhase = false;
/** The clock minute the board last repainted its countdowns for. */
let lastMinute = null;
/** Per board: its dial, the services its rows hold, and what is painted. */
let boards = new WeakMap();

export function attach(boardsEl, options, themeApi) {
  root = boardsEl;
  api = themeApi;
  tickTimer = setInterval(tick, TICK_MS);
  phaseTimer = setInterval(() => {
    latePhase = !latePhase;
    api.render();
  }, REFRESH_MS);
}

export function detach() {
  clearInterval(tickTimer);
  clearInterval(phaseTimer);
  tickTimer = phaseTimer = null;
  for (const dial of document.querySelectorAll('.dial')) dial.remove();
  for (const row of document.querySelectorAll('.row[data-dial]')) delete row.dataset.dial;
  // A WeakMap cannot be emptied; a new one lets the old boards go.
  boards = new WeakMap();
  lastMinute = null;
  latePhase = false;
  root = api = null;
}

/* ------------------------------------------------------------------- time */

/** The board's own clock as minutes of the day and seconds, or the browser's. */
function readClock(scope) {
  const el = (scope || document).querySelector('.clock');
  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec((el?.textContent || '').trim());
  if (match) {
    return { minute: Number(match[1]) * 60 + Number(match[2]), second: Number(match[3] || 0) };
  }
  const now = new Date();
  return { minute: now.getHours() * 60 + now.getMinutes(), second: now.getSeconds() };
}

const HHMM = /^(\d{1,2}):(\d{2})$/;

/** "HH:MM" as minutes of the day, or null for anything else. */
function minutesOf(text) {
  const match = HHMM.exec(text || '');
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

/**
 * When the train goes, and how it should be told. The estimate wins when there
 * is one; "On time", "Delayed" and a missing estimate fall back to the book.
 * `away` is whole minutes from now by the clock's minute: positive is ahead,
 * and a train a minute or two past is still `Due` at its own minute.
 */
function timing(service, nowMinute) {
  const estimated = service.status === 'expected' && minutesOf(service.expected_time) != null;
  const time = estimated ? service.expected_time : service.scheduled_time;
  const minute = minutesOf(time);
  const cancelled = service.status === 'cancelled';
  const late = service.status === 'expected' || service.status === 'delayed';
  // "Delayed" with no estimate has no time to count down to.
  const unknown = service.status === 'delayed' && minutesOf(service.expected_time) == null;
  if (minute == null) return { time: null, away: null, cancelled, late, unknown, minute: null };
  let away = (((minute - nowMinute) % DAY_MINUTES) + DAY_MINUTES) % DAY_MINUTES;
  // Only a little way behind is "just gone"; further round the day is tomorrow.
  if (away > DAY_MINUTES - PAST_WINDOW) away -= DAY_MINUTES;
  return { time, away, cancelled, late, unknown, minute };
}

/** The word for how long, shared by the status column and the dial. */
function countdown(away) {
  return away <= 0 ? 'Due' : `${away} min`;
}

/** board.js asks for every status cell; the wording counts down like the dial. */
export function statusText(service) {
  if (service.status === 'cancelled') return 'Cancelled';
  const t = timing(service, readClock().minute);
  if (t.unknown) return 'Delayed';
  if (t.away == null) return null;
  if (t.away >= HORIZON_MINUTES) return t.late ? `Exp ${t.time}` : t.time;
  // The column cannot hold both, so a late train says each in turn.
  if (t.late && latePhase) return `Exp ${t.time}`;
  return countdown(t.away);
}

/* ------------------------------------------------------------------ rows */

function stateFor(boardEl) {
  let state = boards.get(boardEl);
  if (!state) {
    state = { entries: new Map(), dial: null, key: null, angle: null };
    boards.set(boardEl, state);
  }
  return state;
}

/** board.js hands over each row's service once the row is in the tree. */
export function renderRow(row, service, mode) {
  const boardEl = row.closest('.board');
  if (!boardEl) return;
  stateFor(boardEl).entries.set(service.id, { service, row, mode });
}

/** The services in row order, forgetting any whose row has gone. */
function orderedEntries(state) {
  const live = [];
  for (const [id, entry] of state.entries) {
    if (entry.row.isConnected) live.push(entry);
    else state.entries.delete(id);
  }
  return live.sort((a, b) => Number(a.row.dataset.index) - Number(b.row.dataset.index));
}

/* ------------------------------------------------------------------ model */

/** Where a minute of the hour sits on the dial, as a point at radius r. */
function polar(radius, minuteOfHour) {
  const angle = (minuteOfHour % 60) * (Math.PI / 30);
  return [radius * Math.sin(angle), -radius * Math.cos(angle)];
}

const round = (n) => Math.round(n * 100) / 100;

/**
 * Everything the dial paints, as plain data so one comparison says whether
 * anything but the hand needs touching. Minute resolution throughout: the
 * markers, the arc and the countdown all change on a minute, never between.
 */
function buildModel(entries, nowMinute) {
  const items = entries.map((entry) => ({ entry, t: timing(entry.service, nowMinute) }));
  const next = items.find((item) => !item.t.cancelled) || null;
  const first = items[0] || null;

  const visible = items.filter(
    (item) => item.t.away != null && item.t.away >= -GRACE_MINUTES && item.t.away < HORIZON_MINUTES,
  );
  // The next train claims the first ring, then the rest in time order; a
  // marker takes the second ring if its neighbour minute is already taken.
  const ordered = [...visible].sort((a, b) => {
    if (a === next) return -1;
    if (b === next) return 1;
    return a.t.away - b.t.away;
  });
  const taken = [[], []];
  const conflicts = (ring, minute) => taken[ring].some((m) => {
    const gap = Math.abs(m - minute) % 60;
    return Math.min(gap, 60 - gap) <= 1;
  });
  const markers = ordered.map((item) => {
    const minute = item.t.minute % 60;
    const ring = conflicts(0, minute) && !conflicts(1, minute) ? 1 : 0;
    taken[ring].push(minute);
    const { service } = item.entry;
    const platform = service.platform && service.platform !== '-' ? String(service.platform) : '';
    const kind = item === next ? 'next' : item.t.cancelled ? 'cancelled' : item.t.late ? 'late' : 'normal';
    return { minute, ring, kind, platform: platform.length <= PLATFORM_FIT ? platform : '' };
  });

  // The wait, drawn: from the minute it is now to the minute the next train
  // goes. Nothing to draw once it is due, or when no time is known.
  const nextVisible = next && visible.includes(next);
  const arc = nextVisible && !next.t.unknown && next.t.away > 0
    ? { from: nowMinute % 60, span: next.t.away }
    : null;

  return { markers, arc, centre: centreModel(first, next, items), items, next, visible };
}

function centreModel(first, next, items) {
  if (!items.length) return { kind: 'none' };
  const mode = items[0].entry.mode;
  const placeOf = (item) => {
    const { service } = item.entry;
    return (mode === 'arrivals' ? service.origin : service.destination) || '';
  };
  const label = mode === 'arrivals' ? 'next arrival' : 'next train';
  // Every train on the board is cancelled: there is no wait to count.
  if (!next) {
    return { kind: 'cancelled', big: 'Cancelled', label, labelKind: '', place: placeOf(first), platform: '', unit: '' };
  }
  const platform = next.entry.service.platform;
  const model = {
    label,
    labelKind: '',
    place: placeOf(next),
    platform: platform && platform !== '-' ? `Platform ${platform}` : '',
    unit: '',
  };
  // The train that was next has gone; say so, then count to the one that is.
  if (first !== next) {
    model.label = `${first.t.time || first.entry.service.scheduled_time || ''} cancelled`.trim();
    model.labelKind = 'cancelled';
  }
  if (next.t.unknown) return { ...model, kind: 'delayed', big: 'Delayed' };
  if (next.t.away == null) return { ...model, kind: 'none' };
  if (next.t.away >= HORIZON_MINUTES) return { ...model, kind: 'time', big: next.t.time };
  if (next.t.away <= 0) return { ...model, kind: 'due', big: 'Due' };
  return { ...model, kind: 'count', big: String(next.t.away), unit: 'min' };
}

/* -------------------------------------------------------------------- DOM */

function svg(tag, attrs = {}, text = null) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
  if (text != null) el.textContent = text;
  return el;
}

function html(tag, className) {
  const el = document.createElement(tag);
  el.className = className;
  return el;
}

/** The sixty ticks as two paths, so the face is two nodes and not sixty. */
function buildTicks() {
  let minor = '';
  let major = '';
  for (let minute = 0; minute < 60; minute += 1) {
    const isMajor = minute % 5 === 0;
    const [x1, y1] = polar(isMajor ? TICK_MAJOR_FROM : TICK_MINOR_FROM, minute);
    const [x2, y2] = polar(TICK_OUTER, minute);
    const segment = `M${round(x1)} ${round(y1)}L${round(x2)} ${round(y2)}`;
    if (isMajor) major += segment;
    else minor += segment;
  }
  const ticks = svg('g', { class: 'dial-ticks' });
  ticks.append(svg('path', { class: 'dial-tick', d: minor }), svg('path', { class: 'dial-tick dial-tick-major', d: major }));
  return ticks;
}

function buildDial() {
  const dial = html('div', 'dial');
  dial.setAttribute('aria-hidden', 'true');
  const face = html('div', 'dial-face');

  const canvas = svg('svg', { viewBox: `${-EDGE} ${-EDGE} ${EDGE * 2} ${EDGE * 2}` });
  const hand = svg('g', { class: 'dial-hand' });
  hand.append(svg('line', { x1: 0, y1: -HAND_FROM, x2: 0, y2: -HAND_TO }));
  canvas.append(
    buildTicks(),
    svg('path', { class: 'dial-arc', d: '' }),
    svg('g', { class: 'dial-markers' }),
    hand,
  );

  // Over the SVG rather than in it: HTML text ellipsises, and sizes itself in
  // the dial's own container units.
  const centre = html('div', 'dial-centre');
  centre.dataset.kind = 'none';
  const count = html('div', 'dial-count');
  count.append(html('span', 'dial-big'), html('span', 'dial-unit'));
  centre.append(html('div', 'dial-label'), count, html('div', 'dial-place'), html('div', 'dial-platform'));

  face.append(canvas, centre);
  dial.append(face);
  return dial;
}

function ensureDial(boardEl, state) {
  if (state.dial?.isConnected) return state.dial;
  let dial = boardEl.querySelector(':scope > .dial');
  if (!dial) {
    dial = buildDial();
    boardEl.insertBefore(dial, boardEl.querySelector('.rows'));
  }
  state.dial = dial;
  state.key = null;
  state.angle = null;
  return dial;
}

function paintMarkers(dial, markers) {
  const layer = dial.querySelector('.dial-markers');
  layer.textContent = '';
  // The next train goes on last, so it is never covered by a neighbour.
  for (const marker of [...markers].reverse()) {
    const radius = marker.kind === 'next' ? R_NEXT : R_OTHER;
    const [x, y] = polar(RINGS[marker.ring], marker.minute);
    const group = svg('g', { class: 'dial-marker', transform: `translate(${round(x)} ${round(y)})` });
    group.dataset.kind = marker.kind;
    group.append(svg('circle', { r: radius }));
    if (marker.platform) {
      group.append(svg('text', { class: 'dial-plat', 'font-size': marker.kind === 'next' ? 11 : 8.5 }, marker.platform));
    }
    layer.append(group);
  }
}

function paintArc(dial, arc) {
  const path = dial.querySelector('.dial-arc');
  if (!arc) {
    path.setAttribute('d', '');
    return;
  }
  const radius = RINGS[0];
  const [x1, y1] = polar(radius, arc.from);
  const [x2, y2] = polar(radius, arc.from + arc.span);
  const large = arc.span > 30 ? 1 : 0;
  path.setAttribute('d', `M${round(x1)} ${round(y1)}A${radius} ${radius} 0 ${large} 1 ${round(x2)} ${round(y2)}`);
}

function paintCentre(dial, centre) {
  const el = dial.querySelector('.dial-centre');
  el.dataset.kind = centre.kind;
  el.dataset.labelKind = centre.labelKind || '';
  const set = (selector, text) => {
    const target = el.querySelector(selector);
    if (target.textContent !== (text || '')) target.textContent = text || '';
  };
  const empty = centre.kind === 'none';
  set('.dial-label', empty ? '' : centre.label);
  set('.dial-big', empty ? '' : centre.big);
  set('.dial-unit', empty ? '' : centre.unit);
  set('.dial-place', empty ? '' : centre.place);
  set('.dial-platform', empty ? '' : centre.platform);
}

/** Pair each list row with its marker: the chip before the time reads this. */
function markRows(model) {
  for (const item of model.items) {
    const kind = item === model.next ? 'next' : model.visible.includes(item) ? 'on' : 'off';
    if (item.entry.row.dataset.dial !== kind) item.entry.row.dataset.dial = kind;
  }
}

/** Turn the hand, keeping the angle continuous so it never spins back at 12. */
function moveHand(dial, state, clock) {
  const hand = dial.querySelector('.dial-hand');
  let angle = (clock.minute % 60) * 6 + clock.second * 0.1;
  if (state.angle != null) angle += 360 * Math.round((state.angle - angle) / 360);
  if (angle === state.angle) return;
  // The sweep is eased by CSS, but not on the first placing: that would
  // spin the hand in from nought on every board that appears.
  if (state.angle == null) delete hand.dataset.live;
  else hand.dataset.live = '1';
  state.angle = angle;
  hand.style.transform = `rotate(${round(angle)}deg)`;
}

function updateBoard(boardEl) {
  const state = stateFor(boardEl);
  const dial = ensureDial(boardEl, state);
  const clock = readClock(boardEl);
  const model = buildModel(orderedEntries(state), clock.minute);
  moveHand(dial, state, clock);

  // The hand moves every second; nothing else needs touching unless a minute
  // or a train changed what the face says.
  const key = JSON.stringify([model.markers, model.arc, model.centre]);
  if (key !== state.key) {
    state.key = key;
    paintMarkers(dial, model.markers);
    paintArc(dial, model.arc);
    paintCentre(dial, model.centre);
  }
  markRows(model);
}

/* ------------------------------------------------------------------ hooks */

export function afterRender(boardsEl) {
  for (const boardEl of boardsEl.querySelectorAll(':scope > .board')) updateBoard(boardEl);
  lastMinute = readClock().minute;
}

function tick() {
  if (!root) return;
  for (const boardEl of root.querySelectorAll(':scope > .board')) updateBoard(boardEl);
  // The status column's countdowns are board.js text, so a new minute needs a
  // pass of its own; between minutes there is nothing for it to say.
  const minute = readClock().minute;
  if (minute !== lastMinute) {
    lastMinute = minute;
    api.render();
  }
}
