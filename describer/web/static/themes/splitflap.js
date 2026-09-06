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

const flaps = new Set();
let flapMs = 40;
let clickEnabled = false;
let audio = null;
let frame = null;

export function attach(_boardsEl, options) {
  configure(options);
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
  document.body.style.removeProperty('--flap-ms');
  if (audio) { audio.close(); audio = null; }
}

/** board.js calls this for every cell instead of setting textContent. */
export function renderText(cell, text) {
  const width = Number(getComputedStyle(cell).getPropertyValue('--chars')) || text.length || 1;
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

function normalise(text, width) {
  return String(text).toUpperCase().slice(0, width).padEnd(width, ' ');
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
