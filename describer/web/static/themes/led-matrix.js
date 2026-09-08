import { CHAR_W, CHAR_H, columnsFor, normalise } from './dotmatrix.js';

/**
 * led-matrix theme: the amber LED dot-matrix boards of the 2000s, which
 * replaced the flip-dots on
 * British platforms, and stayed there until the flat panels arrived.
 *
 * The whole screen is one panel, so everything on it is made of dots: the
 * headings, the station, the clock, the stops and the messages. An LED has no
 * moving part, so a change simply appears, and a line too long for the panel
 * scrolls a column of dots at a time rather than turning a page.
 *
 * There is no animation frame here at all. Painting is synchronous, driven by
 * the scroll timer and by whatever changed, which is both cheaper than a frame
 * loop and immune to the compositor deciding to stop delivering frames.
 */

/** Dot pitch as a share of the font size, so a character is 0.6em by 0.8em. */
const PITCH_EM = 0.1;
/** The lit core of an LED, as a share of the dot pitch. */
const CORE = 0.66;
/** The bloom around a lit LED, as a multiple of its core, and how faint. */
const HALO = 2.1;
const HALO_ALPHA = 0.13;
/** Amber, and the barely-there glint off an LED that is switched off. */
const LIT = '#ffa61f';
const UNLIT = 'rgba(255, 166, 31, 0.10)';
/** Columns of dots a scrolling line advances per tick, and how often. */
const SCROLL_MS = 45;
/** Blank dots between the end of a scrolling line and its next pass. */
const SCROLL_GAP = 10 * CHAR_W;
/** How often the dot clock reads back the clock board.js is writing. */
const CLOCK_MS = 500;
/** Longest each mirrored field may run before it is abbreviated. */
const IDENT_CHARS = { station: 20, mode: 10, clock: 8, empty: 30, connection: 24 };
const SEPARATOR = '   ';

const HEADINGS = {
  departures: ['Time', 'Destination', 'Plat', 'Expected'],
  arrivals: ['Time', 'Origin', 'Plat', 'Expected'],
};

/** Every canvas we own, and the ones whose text is on the move. */
const cells = new Set();
const scrolling = new Set();
/**
 * Cells asked for before the stylesheet arrived: board.js will not ask again
 * for text that has not changed, so afterRender paints these once it can.
 */
const pending = new Map();
let scrollTimer = null;
let clockTimer = null;
let observer = null;
let root = null;

export function attach(boardsEl) {
  root = boardsEl;
  observer = new ResizeObserver((entries) => {
    for (const entry of entries) fitCanvas(entry.target, entry.contentRect);
  });
  scrollTimer = setInterval(scrollTick, SCROLL_MS);
  // board.js writes the clock straight into .clock, with no theme hook, so the
  // dot clock reads it back rather than being told. The connection warning is
  // read back on the same timer: board.js only toggles the overlay's hidden
  // flag from the SSE handlers, so there is no render pass to hook.
  clockTimer = setInterval(readBack, CLOCK_MS);
}

export function detach() {
  clearInterval(scrollTimer);
  clearInterval(clockTimer);
  scrollTimer = clockTimer = null;
  root = null;
  observer?.disconnect();
  observer = null;
  for (const canvas of cells) canvas.remove();
  // Every line of dots this theme added to mirror text board.js paints as
  // plain words for the themes that have no matrix.
  for (const el of document.querySelectorAll(
    '.dm-columns, .dm-header, .dm-message, .dm-label, .dm-empty, .dm-stale, .dm-connection',
  )) {
    el.remove();
  }
  cells.clear();
  scrolling.clear();
  pending.clear();
}

/** board.js calls this for every cell instead of setting textContent. */
export function renderText(cell, text) {
  const declared = getComputedStyle(cell).getPropertyValue('--chars').trim();
  if (declared === '') {
    // No --chars at all means the stylesheet has not loaded yet; try again
    // after this pass, because board.js will not ask a second time.
    pending.set(cell, text);
    cell.textContent = '';
    return;
  }
  pending.delete(cell);
  const chars = Number(declared) || 0;
  // A column the panel does not have (the operator) stays dark.
  if (!chars) { cell.textContent = ''; return; }
  const right = cell.dataset.field === 'platform';
  paintFixed(cell, text, chars, right);
}

/** The stops run along their own line, scrolling when they do not fit. */
export function renderCallingPoints(list, points) {
  paintScroll(list, points.join(SEPARATOR));
}

/**
 * Why a train is late or cancelled, on the line under its stops. This is the
 * line these panels were bought for: an LED can scroll, so a whole sentence
 * runs through the line rather than being turned a page at a time.
 */
export function renderReason(host, text) {
  paintScroll(host, text);
}

/* ---------------------------------------------------------------- painting */

/** One line of dots at a width the stylesheet chose. */
function paintFixed(host, text, chars, right = false) {
  const canvas = ensureCanvas(host);
  scrolling.delete(canvas);
  canvas.__strip = null;
  setContent(canvas, columnsFor(normalise(text, chars, right)), chars * CHAR_W);
}

/** One line of dots at its natural width, up to `max` characters. */
function paintText(host, text, max) {
  const value = String(text ?? '');
  const chars = Math.max(1, Math.min(value.length, max));
  if (host.dataset.chars !== String(chars)) {
    host.dataset.chars = String(chars);
    host.style.setProperty('--chars', String(chars));
  }
  paintFixed(host, value, chars);
}

/**
 * A full-width line that moves right to left when its text is longer than the
 * line, and stands still when it is not. An LED panel can do this; the
 * flip-dot board next door cannot, and pages instead.
 */
function paintScroll(host, text) {
  const canvas = ensureCanvas(host);
  const wanted = String(text).toUpperCase();
  const width = lineWidth(host);
  if (canvas.__scrollText === wanted && canvas.__lineWidth === width) return;
  canvas.__scrollText = wanted;
  canvas.__lineWidth = width;
  const chars = Math.max(1, Math.floor(width / CHAR_W));
  const columns = columnsFor(normalise(wanted, Math.max(chars, wanted.length)));
  if (columns.length <= width) {
    scrolling.delete(canvas);
    canvas.__strip = null;
    setContent(canvas, columns.concat(new Array(width - columns.length).fill(0)), width);
    return;
  }
  // Longer than the line: keep the whole message as a strip of columns and
  // show a window on to it that moves one column per tick.
  canvas.__strip = columns.concat(new Array(SCROLL_GAP).fill(0));
  canvas.__offset = 0;
  setContent(canvas, canvas.__strip.slice(0, width), width);
  scrolling.add(canvas);
}

function scrollTick() {
  for (const canvas of scrolling) {
    if (!canvas.isConnected) {
      scrolling.delete(canvas);
      cells.delete(canvas);
      continue;
    }
    const strip = canvas.__strip;
    if (!strip) continue;
    canvas.__offset = (canvas.__offset + 1) % strip.length;
    const width = canvas.__cols;
    const view = new Array(width);
    for (let i = 0; i < width; i += 1) view[i] = strip[(canvas.__offset + i) % strip.length];
    canvas.__shown = view;
    paint(canvas);
  }
}

/** How many dot columns the line affords, from the pitch the CSS gave it. */
function lineWidth(host) {
  const available = host.clientWidth || host.parentElement?.clientWidth || 0;
  const pitch = parseFloat(getComputedStyle(host).fontSize) * PITCH_EM || 0;
  if (!available || !pitch) return CHAR_W;
  return Math.max(CHAR_W, Math.floor(available / pitch));
}

/** Light the dots. Nothing moves, so this is the whole of the animation. */
function setContent(canvas, columns, width) {
  if (canvas.__cols === width && same(columns, canvas.__shown)) return;
  canvas.__cols = width;
  canvas.__shown = columns;
  paint(canvas);
}

function same(a, b) {
  if (!b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}

/**
 * Three passes over the grid: the dark glint of the LEDs that are off, the
 * bloom around the ones that are on, and their lit cores. Built as three
 * paths and filled three times, so the cost is in the dots, not in the calls.
 */
function paint(canvas) {
  const ctx = canvas.getContext('2d');
  const { width, height } = canvas;
  const cols = canvas.__cols || 1;
  const pitch = Math.min(width / cols, height / CHAR_H);
  const radius = (pitch * CORE) / 2;
  const x0 = (width - pitch * cols) / 2;
  const y0 = (height - pitch * CHAR_H) / 2;
  const shown = canvas.__shown || [];

  ctx.clearRect(0, 0, width, height);
  if (pitch < 1.5) {
    // Too small for round LEDs: fill the lit dots as squares so it still reads.
    ctx.fillStyle = LIT;
    for (let x = 0; x < cols; x += 1) {
      for (let y = 0; y < 7; y += 1) {
        if (shown[x] & (1 << y)) ctx.fillRect(x0 + x * pitch, y0 + y * pitch, pitch, pitch);
      }
    }
    return;
  }

  const dark = new Path2D();
  const bloom = new Path2D();
  const core = new Path2D();
  for (let x = 0; x < cols; x += 1) {
    const cx = x0 + (x + 0.5) * pitch;
    const column = shown[x] || 0;
    for (let y = 0; y < 7; y += 1) {
      const cy = y0 + (y + 0.5) * pitch;
      if (column & (1 << y)) {
        bloom.moveTo(cx + radius * HALO, cy);
        bloom.arc(cx, cy, radius * HALO, 0, Math.PI * 2);
        core.moveTo(cx + radius, cy);
        core.arc(cx, cy, radius, 0, Math.PI * 2);
      } else {
        dark.moveTo(cx + radius, cy);
        dark.arc(cx, cy, radius, 0, Math.PI * 2);
      }
    }
  }
  ctx.fillStyle = UNLIT;
  ctx.fill(dark);
  ctx.fillStyle = LIT;
  ctx.globalAlpha = HALO_ALPHA;
  ctx.fill(bloom);
  ctx.globalAlpha = 1;
  ctx.fill(core);
}

/* --------------------------------------------------------------- canvases */

function ensureCanvas(host) {
  let canvas = host.firstElementChild;
  if (!canvas || canvas.tagName !== 'CANVAS') {
    host.textContent = '';
    canvas = document.createElement('canvas');
    canvas.className = 'dots';
    canvas.__cols = 0;
    host.append(canvas);
    fitCanvas(canvas, canvas.getBoundingClientRect());
    observer?.observe(canvas);
  }
  cells.add(canvas);
  return canvas;
}

/** Match the backing store to the CSS box so the LEDs are drawn crisp. */
function fitCanvas(canvas, rect) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(rect.width * dpr));
  const h = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width === w && canvas.height === h) return;
  canvas.width = w;
  canvas.height = h;
  if (canvas.__shown) paint(canvas);
}

/* ----------------------------------------------------------------- panels */

/**
 * Everything on the panel is dots, including the words board.js writes into
 * its own elements with no theme hook. The stylesheet hides those, and each
 * one is mirrored here into a line this theme owns.
 */
export function afterRender(boardsEl) {
  for (const [cell, text] of Array.from(pending)) {
    pending.delete(cell);
    if (cell.isConnected) renderText(cell, text);
  }
  root = boardsEl;
  for (const board of boardsEl.querySelectorAll('.board')) {
    paintHeader(board);
    paintHeadings(board);
    paintStale(board);
    paintLabel(board);
    paintMessage(board);
    paintEmpty(board);
  }
  paintConnection();
}

/** The top line of the panel: which station, which way, and the time. */
function paintHeader(board) {
  const header = board.querySelector('.board-header');
  let line = header.querySelector('.dm-header');
  if (!line) {
    line = document.createElement('div');
    line.className = 'dm-header';
    for (const part of ['station', 'mode', 'clock']) {
      const span = document.createElement('span');
      span.className = `dm-${part}`;
      line.append(span);
    }
    header.append(line);
  }
  paintText(line.querySelector('.dm-station'), board.querySelector('.station-name').textContent, IDENT_CHARS.station);
  paintText(line.querySelector('.dm-mode'), board.querySelector('.board-mode').textContent, IDENT_CHARS.mode);
  paintClock(board, line.querySelector('.dm-clock'));
}

/** Everything board.js writes with no theme hook, read back on a timer. */
function readBack() {
  paintClocks();
  paintConnection();
}

/** Every board's clock, read back from board.js on its own timer. */
function paintClocks() {
  for (const board of root?.querySelectorAll('.board') || []) {
    const clock = board.querySelector('.dm-clock');
    if (clock) paintClock(board, clock);
  }
}

function paintClock(board, host) {
  const source = board.querySelector('.clock');
  host.hidden = source.hidden;
  if (source.hidden) return;
  paintText(host, source.textContent, IDENT_CHARS.clock);
}

/** Column headings, in the same dots and at the same pitch as the services. */
function paintHeadings(board) {
  const rows = board.querySelector('.rows');
  let head = rows.querySelector('.dm-columns');
  if (!head) {
    head = document.createElement('div');
    head.className = 'dm-columns';
    for (const cls of ['time', 'destination', 'platform', 'status']) {
      const cell = document.createElement('span');
      cell.className = `label ${cls}`;
      head.append(cell);
    }
  }
  if (rows.firstElementChild !== head) rows.prepend(head);
  const labels = HEADINGS[board.dataset.mode] || HEADINGS.departures;
  Array.from(head.children).forEach((cell, i) => {
    paintText(cell, labels[i], labels[i].length);
  });
}

/**
 * A fault is lit like everything else. There is no second surface on this
 * board to write it on: the panel is the whole screen, so the warning takes
 * the top line of the matrix, under the headings, and scrolls when it is too
 * long for it. A healthy board gives the height back.
 */
function paintStale(board) {
  const rows = board.querySelector('.rows');
  const source = board.querySelector('.stale');
  let line = rows.querySelector('.dm-stale');
  if (!line) {
    line = document.createElement('div');
    line.className = 'dm-stale';
  }
  // Under the headings and above the first service. board.js appends the rows
  // on every pass, so anything inserted here stays ahead of them; the line is
  // kept in the tree at no height when there is nothing wrong.
  const head = rows.querySelector('.dm-columns');
  if (head) { if (head.nextElementSibling !== line) head.after(line); }
  else if (rows.firstElementChild !== line) rows.prepend(line);

  const text = source.hidden ? '' : source.textContent;
  rows.style.setProperty('--stale-share', text ? '0.9' : '0');
  if (!text) {
    line.textContent = '';
    return;
  }
  paintScroll(line, text);
}

/**
 * The reconnecting warning. board.js only toggles this overlay's hidden flag
 * from the SSE handlers, so there is no render pass to hang it off; readBack
 * paints it on the clock's timer instead. The stylesheet sizes the words to
 * nothing, so what shows is the LEDs.
 */
function paintConnection() {
  const source = document.getElementById('connection');
  if (!source) return;
  let host = source.querySelector('.dm-connection');
  if (!host) {
    host = document.createElement('span');
    host.className = 'dm-connection';
    source.append(host);
  }
  // The ellipsis has no glyph in a 5x7 font, and would only cost dot columns.
  paintText(host, source.textContent.replace(/\u2026/g, '').trim(), IDENT_CHARS.connection);
}

/** "Calling at" is on the panel too, so it is lit like the stops beside it. */
function paintLabel(board) {
  const wrap = board.querySelector('.calling-points');
  let label = wrap.querySelector('.dm-label');
  if (!label) {
    label = document.createElement('span');
    label.className = 'dm-label';
    wrap.prepend(label);
  }
  paintText(label, wrap.querySelector('.calling-points-label').textContent, 12);
}

/** Service messages, on their own line, scrolling when they do not fit. */
function paintMessage(board) {
  const rows = board.querySelector('.rows');
  const source = board.querySelector('.messages');
  let line = rows.querySelector('.dm-message');
  if (!line) {
    line = document.createElement('div');
    line.className = 'dm-message';
  }
  // append() also re-orders: the message line stays below the service rows.
  rows.append(line);
  const text = source.hidden ? '' : source.textContent;
  rows.style.setProperty('--message-share', text ? '0.9' : '0');
  if (!text) {
    line.textContent = '';
    return;
  }
  paintScroll(line, text);
}

/** A board with no trains says so in dots, like everything else. */
function paintEmpty(board) {
  const rows = board.querySelector('.rows');
  const source = rows.querySelector('.empty');
  let host = rows.querySelector('.dm-empty');
  if (!source) {
    host?.remove();
    return;
  }
  if (!host) {
    host = document.createElement('div');
    host.className = 'dm-empty';
  }
  source.after(host);
  paintText(host, source.textContent, IDENT_CHARS.empty);
}
