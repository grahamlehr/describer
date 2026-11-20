/**
 * dial theme: a station clock with the next hour marked on its rim.
 *
 * Each board carries a proper analogue clock: sixty ticks, the twelve
 * numerals, an hour, a minute and a second hand. Every train leaving in the
 * next hour is one identical slim bar just outside the ticks, at the minute it
 * goes, so the gap between the minute hand and a bar is how long you have; an
 * arc in the accent colour draws that wait. The bars say nothing: colour only
 * (the next running train in the accent, a late one amber, a cancelled one
 * hollow, the rest ivory). What they mean is spelled out in a panel beside the
 * clock, in full and never truncated: how long, where to, which platform, and
 * the waits after that. The ordinary list sits under or beside both and is
 * counted down in the same words.
 *
 * What needs JavaScript:
 *  - the clock and the panel, one pair per board, built lazily in afterRender
 *    because boards come and go as the station count changes between one and
 *    two;
 *  - which service each row is (renderRow), so the bars know the trains;
 *  - the countdown wording of the status column, which has to move with the
 *    clock, and the late trains' alternation of "N min" with "Exp HH:MM".
 *
 * "Now" is read back from the `.clock` element, never from the browser's own
 * clock: board.js keeps that against the Pi, and a kiosk whose own clock is
 * a few minutes out must still show the right time and the right bars.
 */

/** The hands move on, and the clock is read again, this often. */
const TICK_MS = 500;
/** How often a late train's status flips between "N min" and "Exp HH:MM". */
const REFRESH_MS = 15000;
/** A train this far in the past still sits at its minute: it is leaving. */
const GRACE_MINUTES = 2;
/** The rim shows the coming hour; further away than this gets no bar. */
const HORIZON_MINUTES = 60;
/** The clock has moved a whole day if a "past" train is this far behind. */
const PAST_WINDOW = 30;
const DAY_MINUTES = 24 * 60;
/** How many later waits the panel's last line may list. */
const THEN_COUNT = 3;

const SVG_NS = 'http://www.w3.org/2000/svg';

/*
 * Geometry, in viewBox units with the centre of the clock at 0,0. The face
 * (bezel, ticks, numerals) takes the middle three quarters; the arc and the
 * bars live in the margin outside it, so the whole square is the clock and
 * nothing is clipped. Radii are what the CSS shares with this file: if you
 * move one, look at the stroke widths in dial.css too.
 */
const EDGE = 100;
const BEZEL = 74;
const TICK_OUTER = 72;
const TICK_MINOR_FROM = 68.5;
const TICK_MAJOR_FROM = 65;
const NUMERAL_RADIUS = 46;
const NUMERAL_SIZE = 13;
/** The wait is drawn hugging the outside of the bezel. */
const ARC_RADIUS = 77.5;
/** Bars: inner and outer radius of each ring. A second train in a minute takes the next ring. */
const BAR_RINGS = [[80.5, 89], [91, 99.5]];
const BAR_WIDTH = 3.4;

let root = null;
let api = null;
let tickTimer = null;
let phaseTimer = null;
/** Shared by every late service on screen, so they flip together. */
let latePhase = false;
/** The clock minute the board last repainted its countdowns for. */
let lastMinute = null;
/** Per board: its clock and panel, the services its rows hold, and what is painted. */
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
  for (const el of document.querySelectorAll('.dial, .next')) el.remove();
  for (const row of document.querySelectorAll('.row[data-dial], .row[data-rim]')) {
    delete row.dataset.dial;
    delete row.dataset.rim;
  }
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
  if (minute == null) return { time: null, away: null, cancelled, late, unknown, estimated, minute: null };
  let away = (((minute - nowMinute) % DAY_MINUTES) + DAY_MINUTES) % DAY_MINUTES;
  // Only a little way behind is "just gone"; further round the day is tomorrow.
  if (away > DAY_MINUTES - PAST_WINDOW) away -= DAY_MINUTES;
  return { time, away, cancelled, late, unknown, estimated, minute };
}

/** The word for how long, used by the status column. */
function countdown(away) {
  return away <= 0 ? 'Due' : `${away} min`;
}

/** board.js asks for every status cell; the wording counts down like the panel. */
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
    state = { entries: new Map(), dial: null, panel: null, key: null, angles: null };
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

/** Where a minute of the hour sits on the clock, as a point at radius r. */
function polar(radius, minuteOfHour) {
  const angle = (minuteOfHour % 60) * (Math.PI / 30);
  return [radius * Math.sin(angle), -radius * Math.cos(angle)];
}

const round = (n) => Math.round(n * 100) / 100;

/**
 * Everything the clock and panel paint, as plain data so one comparison says
 * whether anything but the hands needs touching. Minute resolution
 * throughout: the bars, the arc and the countdown change on a minute, never
 * between.
 */
function buildModel(entries, nowMinute) {
  const items = entries.map((entry) => ({ entry, t: timing(entry.service, nowMinute) }));
  const first = items[0] || null;
  // The soonest running train, not merely the first listed: a late train
  // keeps its place in the book but can be overtaken by the one behind it.
  // Ties keep the earlier row.
  const next = items
    .filter((item) => !item.t.cancelled && item.t.away != null)
    .reduce((best, item) => (!best || item.t.away < best.t.away ? item : best), null);

  const visible = items.filter(
    (item) => item.t.away != null && item.t.away >= -GRACE_MINUTES && item.t.away < HORIZON_MINUTES,
  );
  // The next train claims the first ring, then the rest in time order; a bar
  // goes to the next ring out only when another already sits at its minute.
  const ordered = [...visible].sort((a, b) => {
    if (a === next) return -1;
    if (b === next) return 1;
    return a.t.away - b.t.away;
  });
  const taken = BAR_RINGS.map(() => new Set());
  const bars = ordered.map((item) => {
    const minute = item.t.minute % 60;
    let ring = taken.findIndex((minutes) => !minutes.has(minute));
    if (ring < 0) ring = taken.length - 1;
    taken[ring].add(minute);
    return { minute, ring, kind: kindOf(item, next) };
  });

  // The wait, drawn: from the minute it is now to the minute the next train
  // goes. Nothing to draw once it is due, or when no time is known.
  const arc = next && visible.includes(next) && !next.t.unknown && next.t.away > 0
    ? { from: nowMinute % 60, span: next.t.away }
    : null;

  return { bars, arc, panel: panelModel(items, first, next), items, next, visible };
}

/** One word for a train's colour, shared by its rim bar and its list chip. */
function kindOf(item, next) {
  if (item === next) return 'next';
  if (item.t.cancelled) return 'cancelled';
  return item.t.late ? 'late' : 'normal';
}

function placeOf(item) {
  const { service, mode } = item.entry;
  return (mode === 'arrivals' ? service.origin : service.destination) || '';
}

/** "12", "12 and 16", "12, 16 and 20", with the unit once at the end. */
function thenLine(items, next) {
  const waits = items
    .filter((item) => item !== next && !item.t.cancelled && item.t.away != null && item.t.away > next.t.away && item.t.away < HORIZON_MINUTES)
    .map((item) => item.t.away)
    .sort((a, b) => a - b)
    .slice(0, THEN_COUNT)
    .map(String);
  if (!waits.length) return '';
  const last = waits.pop();
  return `then ${waits.length ? `${waits.join(', ')} and ` : ''}${last} min`;
}

function panelModel(items, first, next) {
  if (!first) return { kind: 'none' };
  const mode = first.entry.mode;
  const label = mode === 'arrivals' ? 'Next arrival' : 'Next train';
  const scheduled = (item) => item.entry.service.scheduled_time || '';
  // Every train on the board is cancelled: there is no wait to count.
  if (!next) {
    // No running train with a time to count to, and not every one cancelled: nothing honest to say.
    if (!items.every((item) => item.t.cancelled)) return { kind: 'none' };
    return {
      kind: 'cancelled', cancel: '', label, big: 'Cancelled', unit: '', place: placeOf(first),
      sched: scheduled(first), platform: '', exp: '', then: '',
    };
  }
  const platform = next.entry.service.platform;
  const model = {
    // The first train listed is cancelled: say so, then describe the first running one.
    cancel: first.t.cancelled ? `${scheduled(first)} ${placeOf(first)} cancelled`.trim() : '',
    label,
    unit: '',
    place: placeOf(next),
    sched: scheduled(next),
    platform: platform && platform !== '-' ? `Platform ${platform}` : '',
    exp: next.t.late && next.t.estimated ? `Exp ${next.t.time}` : '',
    then: thenLine(items, next),
  };
  if (next.t.unknown) return { ...model, kind: 'delayed', big: 'Delayed' };
  // Sixty minutes off the big figure is the time itself; saying it again
  // as the scheduled time would be noise, though a late train still differs.
  if (next.t.away >= HORIZON_MINUTES) {
    return { ...model, kind: 'time', big: next.t.time, sched: model.sched === next.t.time ? '' : model.sched };
  }
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

/** 1 to 12, upright, set in the SVG's own units so they scale with the face. */
function buildNumerals() {
  const numerals = svg('g', { class: 'dial-numerals', 'font-size': NUMERAL_SIZE });
  for (let hour = 1; hour <= 12; hour += 1) {
    const [x, y] = polar(NUMERAL_RADIUS, hour * 5);
    numerals.append(svg('text', { x: round(x), y: round(y) }, String(hour)));
  }
  return numerals;
}

function buildHand(name, tail, length) {
  const hand = svg('g', { class: `dial-hand dial-hand-${name}` });
  hand.append(svg('line', { x1: 0, y1: tail, x2: 0, y2: -length }));
  return hand;
}

function buildDial() {
  const dial = html('div', 'dial');
  dial.setAttribute('aria-hidden', 'true');
  const face = html('div', 'dial-face');
  const canvas = svg('svg', { viewBox: `${-EDGE} ${-EDGE} ${EDGE * 2} ${EDGE * 2}` });
  canvas.append(
    svg('circle', { class: 'dial-bezel', r: BEZEL }),
    buildTicks(),
    buildNumerals(),
    svg('path', { class: 'dial-arc', d: '' }),
    svg('g', { class: 'dial-bars' }),
    buildHand('hour', 6, 37),
    buildHand('minute', 8, 61),
    buildHand('second', 14, 65),
    svg('circle', { class: 'dial-hub', r: 3.4 }),
    svg('circle', { class: 'dial-hub-dot', r: 1.5 }),
  );
  face.append(canvas);
  dial.append(face);
  return dial;
}

function buildPanel() {
  const panel = html('div', 'next');
  panel.dataset.kind = 'none';
  const wait = html('div', 'next-wait');
  wait.append(html('span', 'next-big'), html('span', 'next-unit'));
  const facts = html('div', 'next-facts');
  facts.append(html('span', 'next-sched'), html('span', 'next-plat'), html('span', 'next-exp'));
  panel.append(html('div', 'next-cancel'), html('div', 'next-label'), wait, html('div', 'next-place'), facts, html('div', 'next-then'));
  return panel;
}

function ensureBoard(boardEl, state) {
  if (state.dial?.isConnected && state.panel?.isConnected) return;
  const rows = boardEl.querySelector('.rows');
  // Both or neither: a half-built board would leave an orphan after a detach.
  boardEl.querySelectorAll(':scope > .dial, :scope > .next').forEach((el) => el.remove());
  state.dial = buildDial();
  state.panel = buildPanel();
  rows.before(state.dial, state.panel);
  state.key = null;
  state.angles = null;
}

function paintBars(dial, bars) {
  const layer = dial.querySelector('.dial-bars');
  layer.textContent = '';
  for (const bar of bars) {
    const [inner, outer] = BAR_RINGS[bar.ring];
    const el = svg('rect', {
      class: 'dial-bar',
      x: -BAR_WIDTH / 2,
      y: -outer,
      width: BAR_WIDTH,
      height: outer - inner,
      rx: BAR_WIDTH / 2,
      transform: `rotate(${bar.minute * 6})`,
    });
    el.dataset.kind = bar.kind;
    layer.append(el);
  }
}

function paintArc(dial, arc) {
  const path = dial.querySelector('.dial-arc');
  if (!arc) {
    path.setAttribute('d', '');
    return;
  }
  const [x1, y1] = polar(ARC_RADIUS, arc.from);
  const [x2, y2] = polar(ARC_RADIUS, arc.from + arc.span);
  const large = arc.span > 30 ? 1 : 0;
  path.setAttribute('d', `M${round(x1)} ${round(y1)}A${ARC_RADIUS} ${ARC_RADIUS} 0 ${large} 1 ${round(x2)} ${round(y2)}`);
}

function paintPanel(panel, model) {
  panel.dataset.kind = model.kind;
  const empty = model.kind === 'none';
  const set = (selector, text) => {
    const target = panel.querySelector(selector);
    const value = empty ? '' : text || '';
    if (target.textContent !== value) target.textContent = value;
  };
  set('.next-cancel', model.cancel);
  set('.next-label', model.label);
  set('.next-big', model.big);
  set('.next-unit', model.unit);
  set('.next-place', model.place);
  set('.next-sched', model.sched);
  set('.next-plat', model.platform);
  set('.next-exp', model.exp);
  set('.next-then', model.then);
}

/** Pair each list row with its bar: the chip before the time reads this. */
function markRows(model) {
  for (const item of model.items) {
    const kind = kindOf(item, model.next);
    if (item.entry.row.dataset.dial !== kind) item.entry.row.dataset.dial = kind;
    // A train further off than the rim shows has no bar, so its chip is faint.
    if (model.visible.includes(item)) delete item.entry.row.dataset.rim;
    else item.entry.row.dataset.rim = 'off';
  }
}

/**
 * Turn the three hands, keeping each angle continuous so none spins back at
 * 12. The sweep is eased by CSS over the second between updates, but not on
 * the first placing: that would spin every hand in from nought.
 */
function moveHands(dial, state, clock) {
  const targets = {
    hour: (clock.minute % 720) * 0.5 + clock.second / 120,
    minute: (clock.minute % 60) * 6 + clock.second * 0.1,
    second: clock.second * 6,
  };
  const first = state.angles == null;
  if (first) state.angles = {};
  for (const [name, target] of Object.entries(targets)) {
    let angle = target;
    const before = state.angles[name];
    if (before != null) angle += 360 * Math.round((before - angle) / 360);
    if (angle === before) continue;
    const hand = dial.querySelector(`.dial-hand-${name}`);
    if (first) delete hand.dataset.live;
    else hand.dataset.live = '1';
    state.angles[name] = angle;
    hand.style.transform = `rotate(${round(angle)}deg)`;
  }
}

function updateBoard(boardEl) {
  const state = stateFor(boardEl);
  ensureBoard(boardEl, state);
  const clock = readClock(boardEl);
  const model = buildModel(orderedEntries(state), clock.minute);
  moveHands(state.dial, state, clock);

  // The hands move every tick; nothing else needs touching unless a minute or
  // a train changed what the clock says.
  const key = JSON.stringify([model.bars, model.arc, model.panel]);
  if (key !== state.key) {
    state.key = key;
    paintBars(state.dial, model.bars);
    paintArc(state.dial, model.arc);
    paintPanel(state.panel, model.panel);
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
