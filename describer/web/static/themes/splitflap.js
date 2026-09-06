/**
 * splitflap theme: every character is a flap that steps through the alphabet
 * to its target. One rAF loop drives every flap on screen, and each step only
 * animates transform/opacity so the Pi stays composited on the GPU.
 */

const ALPHABET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:'-&/()•";
/** Longest run of flips for one character; keeps a full board settling quickly. */
const MAX_STEPS = 14;
/** Clicks per frame, so a whole board changing at once does not buzz. */
const MAX_CLICKS_PER_FRAME = 3;
/** A delayed service alternates between the word and the time, as Solari boards do. */
const STATUS_CYCLE_MS = 15000;
/** How long one page of calling points holds before the next flips up. */
const CALLING_PAGE_MS = 6000;
const SEPARATOR = ' • ';

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

const flaps = new Set();
let flapMs = 40;
let clickEnabled = false;
let audio = null;
let frame = null;

/** 0 shows the word, 1 shows the time. Shared by every delayed service. */
let statusPhase = 0;
let statusTimer = null;
let callingTimer = null;
let requestRender = null;
const callingLists = new Set();

export function attach(_boardsEl, options, api) {
  configure(options);
  requestRender = api?.render || null;
  statusTimer = setInterval(() => {
    statusPhase ^= 1;
    // The phase lives here, so board.js must ask us again for the wording.
    requestRender?.();
  }, STATUS_CYCLE_MS);
  callingTimer = setInterval(turnCallingPage, CALLING_PAGE_MS);
}

export function configure(options = {}) {
  flapMs = Math.max(10, Number(options.flap_ms) || 40);
  clickEnabled = Boolean(options.click_sound);
  document.body.style.setProperty('--flap-ms', `${flapMs}ms`);
}

export function detach() {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  flaps.clear();
  clearInterval(statusTimer);
  clearInterval(callingTimer);
  statusTimer = callingTimer = requestRender = null;
  callingLists.clear();
  document.body.style.removeProperty('--flap-ms');
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
  paint(cell, text, Number(getComputedStyle(cell).getPropertyValue('--chars')) || text.length || 1);
}

function paint(cell, text, width) {
  const target = normalise(text, width);
  const chars = ensureFlaps(cell, width);

  chars.forEach((flap, index) => {
    const wanted = target[index];
    const wantedIndex = indexOf(wanted);
    if (flap.dataset.target === wanted) return;
    flap.dataset.target = wanted;

    const currentIndex = indexOf(flap.textContent || ' ');
    let distance = (wantedIndex - currentIndex + ALPHABET.length) % ALPHABET.length;
    if (distance === 0) return;
    if (distance > MAX_STEPS) {
      // Start closer so no single flap holds the board up.
      const start = (wantedIndex - MAX_STEPS + ALPHABET.length) % ALPHABET.length;
      flap.textContent = ALPHABET[start];
      distance = MAX_STEPS;
    }
    flap.__index = indexOf(flap.textContent || ' ');
    flap.__remaining = distance;
    // Stagger left-to-right so the row ripples rather than snapping.
    flap.__nextAt = performance.now() + index * (flapMs * 0.35);
    flaps.add(flap);
  });

  start();
}

/* ------------------------------------------------------- calling points */

/**
 * board.js hands us the stops for the top service. A mechanical board cannot
 * scroll, so fill one full-width row and turn the page instead.
 */
export function renderCallingPoints(list, points) {
  const key = points.join(SEPARATOR);
  const width = measureWidth(list);
  if (list.__key !== key || list.__width !== width) {
    list.__key = key;
    list.__width = width;
    list.__pages = paginate(points, width);
    list.__page = 0;
  }
  callingLists.add(list);
  paintPage(list);
}

function paintPage(list) {
  const pages = list.__pages || [];
  if (!pages.length) return;
  paint(list, pages[list.__page % pages.length], list.__width || 1);
}

function turnCallingPage() {
  for (const list of callingLists) {
    if (!list.isConnected) {
      callingLists.delete(list);
      continue;
    }
    if ((list.__pages || []).length < 2) continue;
    list.__page = (list.__page + 1) % list.__pages.length;
    paintPage(list);
  }
}

/** How many flaps fit the row, measured rather than assumed from the CSS. */
function measureWidth(list) {
  const available = list.parentElement ? list.parentElement.clientWidth : 0;
  if (!available) return 0;
  ensureFlaps(list, Math.max(list.children.length, 1));
  const gap = parseFloat(getComputedStyle(list).columnGap) || 0;
  const flap = list.firstElementChild.getBoundingClientRect().width + gap;
  return flap > 0 ? Math.max(1, Math.floor((available + gap) / flap)) : 0;
}

/** Pack stops into full rows without splitting a station name across pages. */
function paginate(points, width) {
  if (!width) return [];
  const pages = [];
  let line = '';
  for (const point of points) {
    const joined = line ? line + SEPARATOR + point : point;
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

function normalise(text, width) {
  return fit(String(text), width).toUpperCase().padEnd(width, ' ');
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

function ensureFlaps(cell, width) {
  let chars = Array.from(cell.children);
  if (chars.length !== width || !cell.firstElementChild?.classList.contains('flap')) {
    cell.textContent = '';
    chars = [];
    for (let i = 0; i < width; i += 1) {
      const flap = document.createElement('span');
      flap.className = 'flap';
      flap.textContent = ' ';
      cell.append(flap);
      chars.push(flap);
    }
  }
  return chars;
}

function start() {
  // Always re-request: a frame that never fired (hidden tab, throttled
  // compositor) must not leave a stale handle wedging the whole board.
  if (frame !== null) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(tick);
}

// Coming back from a hidden tab, pick up any flaps left mid-flight.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && flaps.size) start();
});

function tick(now) {
  let clicks = 0;
  for (const flap of flaps) {
    if (now < flap.__nextAt) continue;
    flap.__index = (flap.__index + 1) % ALPHABET.length;
    flap.textContent = ALPHABET[flap.__index];
    flap.__remaining -= 1;
    flap.__nextAt = now + flapMs;

    // Restart the flip animation for this step.
    flap.classList.remove('stepping');
    void flap.offsetWidth;
    flap.classList.add('stepping');

    if (flap.__remaining <= 0) {
      flaps.delete(flap);
      if (clickEnabled && clicks < MAX_CLICKS_PER_FRAME) { click(); clicks += 1; }
    }
  }
  frame = flaps.size ? requestAnimationFrame(tick) : null;
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
