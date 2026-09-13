/**
 * splitflap theme: every character is a flap that steps through the alphabet
 * to its target. Each cell is one canvas, and every flap on it is blitted from
 * a sprite sheet of tiles drawn once per font size and colour. A step is then
 * three or four drawImage calls: no DOM mutation, no layout, no paint of a
 * text run, and no compositor layer made and thrown away per flip, which is
 * what the DOM version cost and what a Pi 4 could not afford at 25 steps a
 * second across several hundred flaps.
 */

/** The drum: blank, digits, letters, and the three marks a real board carries. */
const ALPHABET = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ&:-";
/** Longest run of flips for one character; keeps a full board settling quickly. */
const MAX_STEPS = 14;
/** Clicks per frame, so a whole board changing at once does not buzz. */
const MAX_CLICKS_PER_FRAME = 3;
/** A delayed service alternates between the word and the time, as Solari boards do. */
const STATUS_CYCLE_MS = 15000;
/** How long one page of calling points holds before the next flips up. */
const CALLING_PAGE_MS = 6000;
/**
 * requestAnimationFrame stops whenever Chromium decides the page is hidden or
 * occluded, which a kiosk compositor can do without warning; a timer keeps
 * the flaps settling (in coarser steps) until frames come back.
 */
const WATCHDOG_MS = 250;
/** Between stops; the drum has no bullet. */
const SEPARATOR = ' - ';
/**
 * A tile's geometry in em of the host's font. splitflap.css sizes the canvas
 * from the same numbers (`--chars` tiles at the pitch, less one gap), so the
 * two must change together.
 */
const TILE_EM = 0.86;
const GAP_EM = 0.08;
const PITCH_EM = TILE_EM + GAP_EM;
/**
 * One step is drawn in three frames spread over `flap_ms`: the old top flap
 * falling towards the hinge, the new bottom flap landing under it, and the
 * tile at rest. Three is enough at 25 steps a second; a frame per screen
 * refresh would cost the Pi a scaled blit per flap per frame for nothing the
 * eye can see.
 */
const PHASES = 3;
/** How much of its half-height a flap in flight is foreshortened to. */
const FLIGHT = 0.55;
const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');

/**
 * How a real board shortens a name that will not fit, in the order it gives
 * ground: each rule is applied only while the name is still too long, so
 * "London Charing Cross" becomes "London Charing X" and stops there.
 */
const ABBREVIATIONS = [
  [/\bCross\b/gi, 'X'],
  [/\bInternational\b/gi, 'Intl'],
  [/\bParkway\b/gi, 'Pkwy'],
  [/\bJunction\b/gi, 'Jn'],
  [/\bTerminal (\d)\b/gi, 'T$1'],
  [/\bStreet\b/gi, 'St'],
  [/\bRoad\b/gi, 'Rd'],
  [/\bAirport\b/gi, 'Aprt'],
  [/\bNorth\b/gi, 'N'],
  [/\bSouth\b/gi, 'S'],
  [/\bEast\b/gi, 'E'],
  [/\bWest\b/gi, 'W'],
  [/\bCentral\b/gi, 'Ctl'],
  // The via clause is the least of the name; lose it before cutting words.
  [/\s+via\s+.*$/i, ''],
];

/** Every canvas we own, still or mid-flip. */
const cells = new Set();
/** Canvases with a flap in flight, or a full repaint owed. */
const flipping = new Set();
/** Sprite sheets by font, size and colour; a handful per board. */
const sheets = new Map();
/**
 * Cells asked for before the stylesheet arrived: board.js will not ask again
 * for text that has not changed, so afterRender paints these once it can.
 */
const pending = new Map();
let flapMs = 40;
let clickEnabled = false;
let audio = null;
let frame = null;
let watchdog = null;
let observer = null;

/** 0 shows the word, 1 shows the time. Shared by every delayed service. */
let statusPhase = 0;
let statusTimer = null;
let callingTimer = null;
let requestRender = null;
/** Every line we are turning pages on: the stops, and the reason under them. */
const pagedHosts = new Set();

export function attach(_boardsEl, options, api) {
  configure(options);
  requestRender = api?.render || null;
  observer = new ResizeObserver((entries) => {
    for (const entry of entries) fitCanvas(entry.target, entry.contentRect);
  });
  statusTimer = setInterval(() => {
    statusPhase ^= 1;
    // The phase lives here, so board.js must ask us again for the wording.
    requestRender?.();
  }, STATUS_CYCLE_MS);
  callingTimer = setInterval(turnPage, CALLING_PAGE_MS);
}

export function configure(options = {}) {
  flapMs = Math.max(10, Number(options.flap_ms) || 40);
  clickEnabled = Boolean(options.click_sound);
}

export function detach() {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  clearTimeout(watchdog);
  watchdog = null;
  observer?.disconnect();
  observer = null;
  for (const canvas of cells) canvas.remove();
  cells.clear();
  flipping.clear();
  sheets.clear();
  pending.clear();
  clearInterval(statusTimer);
  clearInterval(callingTimer);
  statusTimer = callingTimer = requestRender = null;
  pagedHosts.clear();
  if (audio) { audio.close(); audio = null; }
}

/**
 * board.js asks before printing the status column. "Exp 15:23" needs nine
 * flaps and a delayed train is the one row people stare at, so alternate
 * between the word and the time instead of shrinking either.
 */
export function statusText(service) {
  if (service.status !== 'expected' || !service.expected_time) return null;
  return statusPhase ? service.expected_time : 'Delayed';
}

/** board.js calls this for every cell instead of setting textContent. */
export function renderText(cell, text) {
  const style = getComputedStyle(cell);
  const declared = style.getPropertyValue('--chars').trim();
  if (declared === '' && cell.dataset.field) {
    // A board column with no --chars at all means splitflap.css has not
    // loaded yet; try again after this pass.
    pending.set(cell, text);
    cell.textContent = '';
    return;
  }
  pending.delete(cell);
  paint(cell, text, Number(declared) || text.length || 1, style);
}

/** Once the stylesheet is in, paint whatever was asked for before it. */
export function afterRender() {
  for (const [cell, text] of pending) {
    if (cell.isConnected) renderText(cell, text);
    else pending.delete(cell);
  }
  for (const canvas of cells) {
    if (!canvas.isConnected) forget(canvas);
  }
}

function paint(host, text, width, style = getComputedStyle(host)) {
  const target = normalise(text, width);
  const canvas = ensureCanvas(host, width, style);
  const face = `${style.fontStyle} ${style.fontWeight} ${style.fontFamily}`;
  if (canvas.__colour !== style.color || canvas.__face !== face) {
    // The tiles are drawn in the host's colour and face, so a change to
    // either (a row turning cancelled) means every tile on it is redrawn.
    canvas.__colour = style.color;
    canvas.__face = face;
    canvas.__sheet = null;
    canvas.__dirty = true;
  }

  const now = performance.now();
  const { __to: to, __from: from, __remaining: remaining, __stepAt: stepAt, __phase: phase } = canvas;
  for (let i = 0; i < width; i += 1) {
    const wanted = indexOf(target[i]);
    // Already there, or still on its way there. A flap whose target matches
    // but is neither is one that was abandoned mid-flight (the loop stalled,
    // or detach() dropped it); it must be re-driven, not skipped.
    if (to[i] === wanted && (remaining[i] < 0 || flipping.has(canvas))) continue;

    const current = to[i];
    let distance = (wanted - current + ALPHABET.length) % ALPHABET.length;
    if (distance === 0) {
      // Retargeted onto the character it is turning to right now: let this
      // step land and go no further.
      remaining[i] = 0;
      continue;
    }
    from[i] = current;
    if (distance > MAX_STEPS) {
      // Start closer so no single flap holds the board up: the first step
      // turns straight to the character MAX_STEPS short of the target.
      to[i] = (wanted - MAX_STEPS + ALPHABET.length) % ALPHABET.length;
      distance = MAX_STEPS + 1;
    } else {
      to[i] = (current + 1) % ALPHABET.length;
    }
    // Steps still to start after this one.
    remaining[i] = distance - 1;
    // Stagger left-to-right so the row ripples rather than snapping.
    stepAt[i] = now + i * (flapMs * 0.35);
    phase[i] = -1;
  }
  flipping.add(canvas);
  start();
}

/* ------------------------------------------------------- calling points */

/**
 * board.js hands us the stops for the top service. A mechanical board cannot
 * scroll, so fill one full-width row and turn the page instead.
 */
export function renderCallingPoints(list, points) {
  paintPaged(list, list.parentElement, points, SEPARATOR);
}

/**
 * And why that train is late or cancelled. A drum can spell out a sentence
 * as readily as a station name, so it is flapped like everything else and
 * turned a page at a time, in step with the stops above it.
 */
export function renderReason(host, text) {
  paintPaged(host, host, String(text).split(/\s+/).filter(Boolean), ' ');
}

/** Pack `items` into rows of flaps that fit `container`, and show one. */
function paintPaged(host, container, items, separator) {
  const key = items.join(separator);
  const width = measureWidth(host, container);
  if (host.__key !== key || host.__width !== width) {
    host.__key = key;
    host.__width = width;
    host.__pages = paginate(items, width, separator);
    host.__page = 0;
  }
  pagedHosts.add(host);
  paintPage(host);
}

function paintPage(host) {
  const pages = host.__pages || [];
  if (!pages.length) return;
  paint(host, pages[host.__page % pages.length], host.__width || 1);
}

/** Every paged line turns together, so the board reads as one machine. */
function turnPage() {
  for (const host of pagedHosts) {
    if (!host.isConnected) {
      pagedHosts.delete(host);
      continue;
    }
    if ((host.__pages || []).length < 2) continue;
    host.__page = (host.__page + 1) % host.__pages.length;
    paintPage(host);
  }
}

/** How many tiles fit the row, from the host's own type size. */
function measureWidth(host, container) {
  const available = container ? container.clientWidth : 0;
  const em = parseFloat(getComputedStyle(host).fontSize);
  if (!available || !em) return 0;
  return Math.max(1, Math.floor((available + GAP_EM * em) / (PITCH_EM * em)));
}

/** Pack stops, or words, into full rows without splitting one across pages. */
function paginate(points, width, separator) {
  if (!width) return [];
  const pages = [];
  let line = '';
  for (const point of points) {
    const joined = line ? line + separator + point : point;
    if (joined.length <= width) {
      line = joined;
      continue;
    }
    if (line) pages.push(line);
    // A name longer than the whole row is the one case we have to cut.
    line = point.length <= width ? point : point.slice(0, width);
  }
  if (line) pages.push(line);
  return pages;
}

/**
 * Only what is on the drum can be shown: apostrophes vanish (KINGS LYNN) and
 * any other stray character becomes a blank flap.
 */
function normalise(text, width) {
  const printable = String(text).replace(/'/g, '').replace(/[^0-9A-Za-z&:\- ]/g, ' ');
  // Abbreviate before upper-casing: the rules write mixed-case replacements.
  return fit(printable, width).toUpperCase().padEnd(width, ' ');
}

/** Shorten a name only as far as it takes to fit the flaps we have. */
function fit(text, width) {
  if (text.length <= width) return text;
  let shortened = text;
  for (const [pattern, replacement] of ABBREVIATIONS) {
    shortened = shortened.replace(pattern, replacement).trim();
    if (shortened.length <= width) return shortened;
  }
  return shortened.slice(0, width);
}

function indexOf(char) {
  const found = ALPHABET.indexOf(char);
  return found === -1 ? 0 : found;
}

/* ------------------------------------------------------------- canvases */

function ensureCanvas(host, width, style) {
  let canvas = host.firstElementChild;
  if (!canvas || !canvas.classList?.contains('flaps')) {
    host.textContent = '';
    canvas = document.createElement('canvas');
    canvas.className = 'flaps';
    canvas.__chars = 0;
    canvas.__em = 0;
    host.append(canvas);
    observer?.observe(canvas);
  }
  if (canvas.__chars !== width) {
    // The stylesheet names the width of a board column; a paged line is as
    // wide as was measured for it, and the canvas is sized from the same
    // property either way.
    if (Number(style.getPropertyValue('--chars')) !== width) host.style.setProperty('--chars', String(width));
    canvas.__chars = width;
    // What each flap is turning to, from, how many steps it still has after
    // this one (-1 when at rest), when its step began, and the phase drawn.
    canvas.__to = new Uint8Array(width);
    canvas.__from = new Uint8Array(width);
    canvas.__remaining = new Int16Array(width).fill(-1);
    canvas.__stepAt = new Float64Array(width);
    canvas.__phase = new Int8Array(width).fill(PHASES - 1);
    canvas.__dirty = true;
    fitCanvas(canvas, canvas.getBoundingClientRect());
  }
  cells.add(canvas);
  return canvas;
}

/** Keep the backing store at the CSS box, in device pixels. */
function fitCanvas(canvas, rect) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(rect.width * dpr));
  const h = Math.max(1, Math.round(rect.height * dpr));
  const em = (parseFloat(getComputedStyle(canvas).fontSize) || 0) * dpr;
  if (canvas.width !== w || canvas.height !== h || canvas.__em !== em) {
    canvas.width = w;
    canvas.height = h;
    canvas.__em = em;
    canvas.__sheet = null;
    canvas.__dirty = true;
    if (cells.has(canvas)) {
      flipping.add(canvas);
      start();
    }
  }
}

function forget(canvas) {
  observer?.unobserve(canvas);
  cells.delete(canvas);
  flipping.delete(canvas);
}

/**
 * The tiles for one canvas: every character of the drum drawn once, on its
 * flap, at this size, face and colour. Shared between canvases that match,
 * which on a board is nearly all of them.
 */
function sheetFor(canvas) {
  if (canvas.__sheet) return canvas.__sheet;
  const em = canvas.__em;
  const tileH = canvas.height;
  if (!em || tileH < 2) return null;
  const tileW = Math.max(1, Math.round(TILE_EM * em));
  const key = `${canvas.__face}|${em}|${tileH}|${canvas.__colour}`;
  let sheet = sheets.get(key);
  if (!sheet) {
    sheet = document.createElement('canvas');
    sheet.width = tileW * ALPHABET.length;
    sheet.height = tileH;
    sheet.__tileW = tileW;
    const ctx = sheet.getContext('2d');
    const root = getComputedStyle(document.documentElement);
    const colour = (name, fallback) => root.getPropertyValue(name).trim() || fallback;
    const hinge = Math.floor(tileH / 2);
    const edge = Math.max(1, Math.round(tileH * 0.02));
    const radius = Math.max(1, Math.round(em * 0.06));
    ctx.font = `${canvas.__face.split(' ').slice(0, 2).join(' ')} ${em}px ${canvas.__face.split(' ').slice(2).join(' ')}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (let i = 0; i < ALPHABET.length; i += 1) {
      const x = i * tileW;
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(x, 0, tileW, tileH, radius);
      ctx.clip();
      ctx.fillStyle = colour('--flap-top', '#1d1d1d');
      ctx.fillRect(x, 0, tileW, hinge);
      ctx.fillStyle = colour('--flap-bottom', '#101010');
      ctx.fillRect(x, hinge, tileW, tileH - hinge);
      ctx.fillStyle = canvas.__colour;
      ctx.fillText(ALPHABET[i], x + tileW / 2, tileH / 2);
      // The hinge, over the letter: the two flaps never quite meet.
      ctx.fillStyle = colour('--flap-edge', '#000');
      ctx.fillRect(x, hinge - Math.ceil(edge / 2), tileW, edge);
      ctx.restore();
    }
    sheets.set(key, sheet);
  }
  canvas.__sheet = sheet;
  return sheet;
}

/**
 * Draw one flap at one phase of its step. The top half of the new character
 * is always in place behind whatever is falling; the bottom half is the old
 * one until the new flap lands on it.
 */
function drawTile(ctx, sheet, canvas, i, phase) {
  const tileW = sheet.__tileW;
  const tileH = canvas.height;
  const half = Math.floor(tileH / 2);
  const x = Math.round(i * PITCH_EM * canvas.__em);
  const sxTo = canvas.__to[i] * tileW;
  ctx.clearRect(x, 0, tileW, tileH);
  ctx.drawImage(sheet, sxTo, 0, tileW, half, x, 0, tileW, half);
  if (phase >= PHASES - 1) {
    ctx.drawImage(sheet, sxTo, half, tileW, tileH - half, x, half, tileW, tileH - half);
    return;
  }
  const sxFrom = canvas.__from[i] * tileW;
  ctx.drawImage(sheet, sxFrom, half, tileW, tileH - half, x, half, tileW, tileH - half);
  if (phase === 0) {
    // The old top flap, falling: foreshortened against the hinge.
    const h = Math.max(1, Math.round(half * FLIGHT));
    ctx.drawImage(sheet, sxFrom, 0, tileW, half, x, half - h, tileW, h);
  } else {
    // The new bottom flap, landing over the old one.
    const h = Math.max(1, Math.round((tileH - half) * FLIGHT));
    ctx.drawImage(sheet, sxTo, half, tileW, tileH - half, x, half, tileW, h);
  }
}

/* ------------------------------------------------------------ the loop */

function start() {
  // Always re-request: a frame that never fired (hidden tab, throttled
  // compositor) must not leave a stale handle wedging the whole board.
  if (frame !== null) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(tick);
  armWatchdog();
}

/** If no frame arrives in time, step the flaps from a timer instead. */
function armWatchdog() {
  clearTimeout(watchdog);
  watchdog = setTimeout(() => {
    watchdog = null;
    if (flipping.size) tick(performance.now());
  }, WATCHDOG_MS);
}

// Coming back from a hidden tab, pick up any flaps left mid-flight.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && flipping.size) start();
});

// A web font landing after the tiles were drawn means they were drawn in
// the fallback face; draw them again.
document.fonts?.addEventListener('loadingdone', () => {
  if (!cells.size) return;
  sheets.clear();
  for (const canvas of cells) {
    canvas.__sheet = null;
    canvas.__dirty = true;
    flipping.add(canvas);
  }
  start();
});

function tick(now) {
  // Whichever of the frame and the watchdog got here first owns this step;
  // drop the other so the two never run the loop side by side.
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  clearTimeout(watchdog);
  watchdog = null;

  let clicks = 0;
  const still = reduceMotion?.matches;
  for (const canvas of flipping) {
    if (!canvas.isConnected) {
      forget(canvas);
      continue;
    }
    const sheet = sheetFor(canvas);
    if (!sheet) continue; // not sized yet; the ResizeObserver will bring it back
    const ctx = canvas.__ctx || (canvas.__ctx = canvas.getContext('2d'));
    const { __chars: chars, __to: to, __from: from, __remaining: remaining, __stepAt: stepAt, __phase: phase } = canvas;

    if (canvas.__dirty) {
      // A new size, face or colour: every tile at rest is redrawn now, and
      // every one in flight is redrawn at its phase below.
      canvas.__dirty = false;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (let i = 0; i < chars; i += 1) {
        if (remaining[i] < 0) drawTile(ctx, sheet, canvas, i, PHASES - 1);
        else phase[i] = -1;
      }
    }

    let active = false;
    for (let i = 0; i < chars; i += 1) {
      if (remaining[i] < 0) continue;
      if (now < stepAt[i]) {
        active = true;
        continue;
      }
      let elapsed = now - stepAt[i];
      if (elapsed >= flapMs) {
        if (remaining[i] === 0) {
          // The last step has had its time: land it and stop.
          if (phase[i] !== PHASES - 1) drawTile(ctx, sheet, canvas, i, PHASES - 1);
          remaining[i] = -1;
          if (clickEnabled && clicks < MAX_CLICKS_PER_FRAME) { click(); clicks += 1; }
          continue;
        }
        // Stepping is paced by the clock, not by frames: after a stall, a
        // flap takes every step it has missed at once and still lands on
        // its target.
        const skip = Math.min(remaining[i], Math.floor(elapsed / flapMs));
        from[i] = (to[i] + skip - 1) % ALPHABET.length;
        to[i] = (to[i] + skip) % ALPHABET.length;
        remaining[i] -= skip;
        stepAt[i] += skip * flapMs;
        phase[i] = -1;
        elapsed = now - stepAt[i];
      }
      const wanted = still ? PHASES - 1 : Math.min(PHASES - 1, Math.floor((elapsed * PHASES) / flapMs));
      if (wanted !== phase[i]) {
        drawTile(ctx, sheet, canvas, i, wanted);
        phase[i] = wanted;
      }
      active = true;
    }
    if (!active) flipping.delete(canvas);
  }
  if (flipping.size) {
    frame = requestAnimationFrame(tick);
    armWatchdog();
  }
}

function click() {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended') audio.resume();
    // Very short filtered blip: a mechanical tick, not a tone.
    const now = audio.currentTime;
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.type = 'square';
    osc.frequency.setValueAtTime(1800, now);
    gain.gain.setValueAtTime(0.05, now);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.02);
    osc.connect(gain).connect(audio.destination);
    osc.start(now);
    osc.stop(now + 0.025);
  } catch {
    clickEnabled = false; // no audio available; stop trying
  }
}
