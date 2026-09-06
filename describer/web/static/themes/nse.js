import { CHAR_W, CHAR_H, columnsFor, normalise, paginate } from './dotmatrix.js';

/**
 * nse theme: a Network SouthEast flip-dot indicator. Every cell is a canvas
 * showing a 5×7 dot-matrix font. When a cell's text changes the discs are
 * flipped column by column, left to right, the way a real controller writes
 * the sign. Only the canvases that change are repainted; nothing here forces
 * layout or moves the DOM, so the Pi has no per-frame page work to do.
 */

/** Dot pitch as a share of the font size, so a character is 0.6em by 0.8em. */
const PITCH_EM = 0.1;
/** Diameter of a disc as a share of the dot pitch. */
const DISC = 0.82;
/** How long the sweep spends on each column of dots while flipping a cell. */
const COLUMN_MS = 4;
/** How long one page of stops, or of a message, holds before the next. */
const PAGE_MS = 6000;
/** Rattles per frame, so a whole board flipping at once does not roar. */
const MAX_RATTLES_PER_FRAME = 4;
/** Keeps flipping cells settling when Chromium stops delivering frames. */
const WATCHDOG_MS = 250;
/** How often the dot clock reads the clock board.js is writing. */
const CLOCK_MS = 500;
/** Longest each mirrored field may run before it is abbreviated. */
const IDENT_CHARS = { station: 20, mode: 10, clock: 8 };
const SEPARATOR = '  ';

/** Every canvas we own, whether still or mid-sweep. */
const cells = new Set();
/** Canvases whose sweep is not finished. */
const flipping = new Set();
/** Canvases showing one page of something too long for their line. */
const paged = new Set();
let frame = null;
let watchdog = null;
let pageTimer = null;
let observer = null;
let clockTimer = null;
let root = null;
let rattleEnabled = false;
let audio = null;
let onColour = '#f2d21c';
let offColour = '#1c1c1c';

export function attach(boardsEl, options) {
  configure(options);
  root = boardsEl;
  observer = new ResizeObserver((entries) => {
    for (const entry of entries) fitCanvas(entry.target, entry.contentRect);
  });
  pageTimer = setInterval(turnPage, PAGE_MS);
  // board.js writes the clock straight into .clock, with no theme hook, so the
  // dot clock reads it back rather than being told.
  clockTimer = setInterval(paintClocks, CLOCK_MS);
}

export function configure(options = {}) {
  rattleEnabled = Boolean(options.click_sound);
  const palette = {
    yellow: ['#f2d21c', '#1c1c1c'],
    white: ['#f4f4ef', '#1c1c1c'],
    green: ['#9be55a', '#181c14'],
  };
  [onColour, offColour] = palette[options.dot_colour] || palette.yellow;
  // A colour change shows up on the next paint of each cell; nudge them all.
  for (const canvas of cells) canvas.__dirty = true;
  if (cells.size) start();
}

export function detach() {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  clearTimeout(watchdog);
  watchdog = null;
  clearInterval(pageTimer);
  clearInterval(clockTimer);
  pageTimer = clockTimer = null;
  root = null;
  observer?.disconnect();
  observer = null;
  for (const canvas of cells) canvas.remove();
  // Everything this casing added: the printed labels and the lines of dots
  // that mirror text board.js paints as plain text for other themes.
  for (const el of document.querySelectorAll('.nse-columns, .nse-ident, .nse-message, .nse-label')) {
    el.remove();
  }
  cells.clear();
  flipping.clear();
  paged.clear();
  pending.clear();
  if (audio) { audio.close(); audio = null; }
}

/**
 * Cells asked for before the stylesheet arrived: board.js will not ask again
 * for text that has not changed, so afterRender paints these once it can.
 */
const pending = new Map();

/** board.js calls this for every cell instead of setting textContent. */
export function renderText(cell, text) {
  const declared = getComputedStyle(cell).getPropertyValue('--chars').trim();
  if (declared === '') {
    // No --chars at all means nse.css has not loaded yet; try after this pass.
    pending.set(cell, text);
    cell.textContent = '';
    return;
  }
  pending.delete(cell);
  const chars = Number(declared) || 0;
  // A column the frame does not have (the operator, on this board) stays empty.
  if (!chars) { cell.textContent = ''; return; }
  const canvas = ensureCanvas(cell);
  // Platform numbers sit against the right of their column, as on the sign.
  const right = cell.dataset.field === 'platform';
  setTarget(canvas, columnsFor(normalise(text, chars, right)), chars * CHAR_W);
}

/* --------------------------------------------------------- calling points */

/**
 * The stops, turned a page at a time. A flip-dot line cannot scroll: every
 * disc is a fixed place on the board, so a message longer than the line can
 * only be rewritten in whole screenfuls.
 */
export function renderCallingPoints(list, points) {
  paintPaged(list, points, SEPARATOR);
}

/** Pack `items` into pages that fit the line, and show the one in hand. */
function paintPaged(host, items, separator) {
  const canvas = ensureCanvas(host);
  const key = items.join('\u0000');
  const chars = Math.max(1, Math.floor(lineWidth(host) / CHAR_W));
  if (canvas.__pageKey !== key || canvas.__pageChars !== chars) {
    canvas.__pageKey = key;
    canvas.__pageChars = chars;
    canvas.__pages = paginate(items, separator, chars);
    canvas.__page = 0;
  }
  paged.add(canvas);
  showPage(canvas);
}

function showPage(canvas) {
  const pages = canvas.__pages || [];
  if (!pages.length) return;
  const chars = canvas.__pageChars;
  setTarget(canvas, columnsFor(normalise(pages[canvas.__page % pages.length], chars)), chars * CHAR_W);
}

/** Every paged line turns together, so the board reads as one machine. */
function turnPage() {
  for (const canvas of paged) {
    if (!canvas.isConnected) {
      paged.delete(canvas);
      continue;
    }
    if ((canvas.__pages || []).length < 2) continue;
    canvas.__page = (canvas.__page + 1) % canvas.__pages.length;
    showPage(canvas);
  }
}

/** How many dot columns the line affords, from the pitch the CSS gave it. */
function lineWidth(host) {
  const available = host.clientWidth || host.parentElement?.clientWidth || 0;
  const pitch = dotPitch(host);
  if (!available || !pitch) return CHAR_W;
  return Math.max(CHAR_W, Math.floor(available / pitch));
}

/**
 * Paint one line of dots at its natural width, up to `max` characters. The
 * mirrors use this: their text is board.js's, so the width is not in the CSS.
 */
function paintText(host, text, max) {
  const value = String(text ?? '');
  const chars = Math.max(1, Math.min(value.length, max));
  if (host.dataset.chars !== String(chars)) {
    host.dataset.chars = String(chars);
    host.style.setProperty('--chars', String(chars));
  }
  const canvas = ensureCanvas(host);
  setTarget(canvas, columnsFor(normalise(value, chars)), chars * CHAR_W);
}

/* -------------------------------------------------------------- canvases */

function ensureCanvas(host) {
  let canvas = host.firstElementChild;
  if (!canvas || canvas.tagName !== 'CANVAS') {
    host.textContent = '';
    canvas = document.createElement('canvas');
    canvas.className = 'dots';
    canvas.__shown = [];
    canvas.__target = [];
    canvas.__cols = 0;
    host.append(canvas);
    fitCanvas(canvas, canvas.getBoundingClientRect());
    observer?.observe(canvas);
  }
  cells.add(canvas);
  return canvas;
}

/** Match the backing store to the CSS box so discs are drawn crisp. */
function fitCanvas(canvas, rect) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(rect.width * dpr));
  const h = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
    canvas.__dirty = true;
    if (cells.has(canvas)) start();
  }
}

/** One dot of pitch in CSS pixels, from the type size the CSS gave the host. */
function dotPitch(el) {
  const size = parseFloat(getComputedStyle(el).fontSize) || 0;
  return size * PITCH_EM;
}

/**
 * Give a canvas a new bitmap. Columns that differ from what is showing are
 * flipped in a left-to-right sweep; a canvas already mid-sweep is retargeted
 * from wherever it has got to.
 */
function setTarget(canvas, columns, width) {
  if (canvas.__cols !== width) {
    canvas.__cols = width;
    canvas.__shown = new Array(width).fill(0);
    canvas.__dirty = true;
  }
  if (!flipping.has(canvas) && same(columns, canvas.__target) && !canvas.__dirty) return;
  canvas.__target = columns;
  if (same(columns, canvas.__shown)) {
    // Nothing to flip; still repaint if the size or colour moved.
    if (canvas.__dirty) start();
    return;
  }
  // Always sweep from the left again: columns already right are skipped, and
  // a retarget mid-sweep may have changed columns the edge has passed.
  canvas.__sweepStart = performance.now();
  canvas.__sweepCol = 0;
  flipping.add(canvas);
  start();
}

function same(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}

/* ---------------------------------------------------------------- frames */

function start() {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(tick);
  armWatchdog();
}

function armWatchdog() {
  // Never push the deadline back. Re-arming on every call meant the watchdog
  // never fired at all, which is precisely when frames have stopped and it is
  // needed: the board froze part-swept.
  if (watchdog !== null) return;
  watchdog = setTimeout(() => {
    watchdog = null;
    tick(performance.now());
  }, WATCHDOG_MS);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && flipping.size) start();
});

function tick(now) {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  clearTimeout(watchdog);
  watchdog = null;

  let rattles = 0;
  for (const canvas of cells) {
    if (!canvas.isConnected) {
      cells.delete(canvas);
      flipping.delete(canvas);
      paged.delete(canvas);
      continue;
    }
    if (flipping.has(canvas)) {
      // The sweep is paced by the clock, not by frames, so a stall is caught up.
      const reached = Math.floor((now - canvas.__sweepStart) / COLUMN_MS);
      const edge = Math.min(reached, canvas.__cols);
      let changed = false;
      for (let x = canvas.__sweepCol; x < edge; x += 1) {
        if (canvas.__shown[x] !== canvas.__target[x]) {
          canvas.__shown[x] = canvas.__target[x];
          changed = true;
        }
      }
      canvas.__sweepCol = edge;
      if (changed && rattleEnabled && rattles < MAX_RATTLES_PER_FRAME) { rattle(); rattles += 1; }
      if (edge >= canvas.__cols) {
        flipping.delete(canvas);
        canvas.__shown = canvas.__target.slice();
      }
      canvas.__dirty = true;
      paintCanvas(canvas, edge);
    } else if (canvas.__dirty) {
      paintCanvas(canvas, -1);
    }
  }
  if (flipping.size) {
    frame = requestAnimationFrame(tick);
    armWatchdog();
  }
}

/** Draw the discs. `edge` is the column mid-flip, drawn on its side. */
function paintCanvas(canvas, edge) {
  canvas.__dirty = false;
  const ctx = canvas.getContext('2d');
  const { width, height } = canvas;
  const cols = canvas.__cols || 1;
  const pitch = Math.min(width / cols, height / CHAR_H);
  const radius = (pitch * DISC) / 2;
  const x0 = (width - pitch * cols) / 2;
  const y0 = (height - pitch * CHAR_H) / 2;
  const shown = canvas.__shown;

  ctx.clearRect(0, 0, width, height);
  if (pitch < 1.5) {
    // Too small for discs: fill dots as squares so the text still reads.
    ctx.fillStyle = onColour;
    for (let x = 0; x < cols; x += 1) {
      for (let y = 0; y < 7; y += 1) {
        if (shown[x] & (1 << y)) ctx.fillRect(x0 + x * pitch, y0 + y * pitch, pitch, pitch);
      }
    }
    return;
  }

  for (const on of [false, true]) {
    ctx.fillStyle = on ? onColour : offColour;
    ctx.beginPath();
    for (let x = 0; x < cols; x += 1) {
      const cx = x0 + (x + 0.5) * pitch;
      const column = shown[x];
      const flippingCol = x === edge;
      for (let y = 0; y < 7; y += 1) {
        if (Boolean(column & (1 << y)) !== on) continue;
        const cy = y0 + (y + 0.5) * pitch;
        if (flippingCol) {
          // A disc caught turning: seen edge-on, it is a sliver.
          ctx.moveTo(cx + radius * 0.35, cy);
          ctx.ellipse(cx, cy, radius * 0.35, radius, 0, 0, Math.PI * 2);
        } else {
          ctx.moveTo(cx + radius, cy);
          ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        }
      }
    }
    ctx.fill();
  }
}

/* ---------------------------------------------------------------- sound */

function rattle() {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended') audio.resume();
    // A burst of filtered noise: discs hitting their stops, not a tone.
    const now = audio.currentTime;
    const length = Math.floor(audio.sampleRate * 0.03);
    const buffer = audio.createBuffer(1, length, audio.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) data[i] = (Math.random() * 2 - 1) * (1 - i / length);
    const source = audio.createBufferSource();
    source.buffer = buffer;
    const filter = audio.createBiquadFilter();
    filter.type = 'bandpass';
    filter.frequency.value = 2500;
    filter.Q.value = 0.8;
    const gain = audio.createGain();
    gain.gain.value = 0.08;
    source.connect(filter).connect(gain).connect(audio.destination);
    source.start(now);
  } catch {
    rattleEnabled = false;
  }
}

/* --------------------------------------------------------------- frame */

const HEADINGS = {
  departures: ['Time', 'To', 'Plat', 'Expected'],
  arrivals: ['Time', 'From', 'Plat', 'Expected'],
};

/** Column labels printed on the casing above the matrix, as the real signs had. */
export function afterRender(boardsEl) {
  for (const [cell, text] of Array.from(pending)) {
    pending.delete(cell);
    if (cell.isConnected) renderText(cell, text);
  }
  root = boardsEl;
  for (const board of boardsEl.querySelectorAll('.board')) {
    paintHeadings(board);
    paintLabel(board);
    paintMessage(board);
    paintIdent(board);
  }
}

/** The one thing on the matrix that is printed rather than flipped. */
function paintHeadings(board) {
  const rows = board.querySelector('.rows');
  let head = rows.querySelector('.nse-columns');
  if (!head) {
    head = document.createElement('div');
    head.className = 'nse-columns';
    for (const cls of ['time', 'destination', 'platform', 'status']) {
      const cell = document.createElement('span');
      cell.className = `label ${cls}`;
      head.append(cell);
    }
  }
  if (rows.firstElementChild !== head) rows.prepend(head);
  const labels = HEADINGS[board.dataset.mode] || HEADINGS.departures;
  Array.from(head.children).forEach((cell, i) => {
    if (cell.textContent !== labels[i]) cell.textContent = labels[i];
  });
}

/**
 * "Calling at" belongs to the matrix, so it is flipped like the stops beside
 * it. board.js keeps writing the words into its own element, which the
 * stylesheet hides; this mirrors them into dots.
 */
function paintLabel(board) {
  const wrap = board.querySelector('.calling-points');
  const source = wrap.querySelector('.calling-points-label');
  let label = wrap.querySelector('.nse-label');
  if (!label) {
    label = document.createElement('span');
    label.className = 'nse-label';
    wrap.prepend(label);
  }
  paintText(label, source.textContent, 12);
}

/**
 * Service messages run along their own line of the matrix, turning a page at
 * a time when they are too long for it. A board with nothing to say gives the height back.
 */
function paintMessage(board) {
  const rows = board.querySelector('.rows');
  const source = board.querySelector('.messages');
  let line = rows.querySelector('.nse-message');
  if (!line) {
    line = document.createElement('div');
    line.className = 'nse-message';
  }
  // append() also re-orders: the message line stays below the service rows.
  // It is kept even when empty, because its auto top margin is what holds
  // both it and the identification line against the foot of a short board.
  rows.append(line);
  const text = source.hidden ? '' : source.textContent;
  rows.style.setProperty('--message-share', text ? '0.9' : '0');
  if (!text) {
    // Nothing to say: drop the dots, and the line claims none of the height.
    line.textContent = '';
    return;
  }
  paintPaged(line, text.split(/\s+/).filter(Boolean), ' ');
}

/**
 * The bottom line of the matrix: which station this is, whether it is showing
 * departures or arrivals, and the time. All three change, so all three flip.
 */
function paintIdent(board) {
  const rows = board.querySelector('.rows');
  let ident = rows.querySelector('.nse-ident');
  if (!ident) {
    ident = document.createElement('div');
    ident.className = 'nse-ident';
    for (const part of ['station', 'mode', 'clock']) {
      const span = document.createElement('span');
      span.className = `nse-${part}`;
      ident.append(span);
    }
  }
  // Last, so it reads as the foot of the matrix whatever else is on it.
  rows.append(ident);
  paintText(ident.querySelector('.nse-station'), board.querySelector('.station-name').textContent, IDENT_CHARS.station);
  paintText(ident.querySelector('.nse-mode'), board.querySelector('.board-mode').textContent, IDENT_CHARS.mode);
  paintClock(board, ident.querySelector('.nse-clock'));
}

/** Every board's clock, read back from board.js on its own timer. */
function paintClocks() {
  for (const board of root?.querySelectorAll('.board') || []) {
    const clock = board.querySelector('.nse-clock');
    if (clock) paintClock(board, clock);
  }
}

function paintClock(board, host) {
  const source = board.querySelector('.clock');
  // A board with its clock switched off shows no dots where the clock was.
  host.hidden = source.hidden;
  if (source.hidden) return;
  paintText(host, source.textContent, IDENT_CHARS.clock);
}
