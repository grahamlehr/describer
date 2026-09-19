/**
 * /setup: the phone's half of first-run setup. Four steps (station, key, sound,
 * finish), one visible at a time. Nothing here is saved until Finish, which
 * sends everything in one request; the key test before it only tries the key.
 *
 * The key field and the station search box have no `name`, and this page has no
 * generic form reader: the key goes into the two requests that need it and
 * nowhere else. It is write-only, as on /admin: once saved it is never shown.
 */

import { stationNames, wireStationLookup } from '/static/stationsearch.js';

const wizard = document.getElementById('wizard');
const stationsEl = document.getElementById('stations');
const stationTemplate = document.getElementById('station-template');
const addStationButton = document.getElementById('add-station');
const progressEl = document.getElementById('progress');
const steps = Array.from(wizard.querySelectorAll('.step[data-step]'));
const doneEl = document.getElementById('done');

const keyEl = document.getElementById('key');
const keySavedEl = document.getElementById('key-saved');
const keyErrorEl = document.getElementById('key-error');
const keyResultEl = document.getElementById('key-result');
const testKeyButton = document.getElementById('test-key');
const keyNextButton = document.getElementById('key-next');

const announceEl = document.getElementById('announce');
const deviceEl = document.getElementById('audio-device');
const soundOptionsEl = document.getElementById('sound-options');
const soundResultEl = document.getElementById('sound-result');
const playTestButton = document.getElementById('play-test');

const summaryEl = document.getElementById('summary');
const finishErrorEl = document.getElementById('finish-error');
const finishButton = document.getElementById('finish');

const MODE_TEXT = { departures: 'trains leaving', arrivals: 'trains arriving' };
const DEVICE_TEXT = { hdmi: 'the TV', jack: 'the 3.5 mm socket', default: 'the default output' };

let step = 1;
/** A key is already saved on the Pi: a blank key box then means "keep it". */
let keySaved = false;
/** The key that last passed the test, so an edited key must be tested again. */
let testedKey = null;
let radioGroups = 0;

/* ------------------------------------------------------------------ steps */

function show(n) {
  step = n;
  for (const section of steps) section.hidden = Number(section.dataset.step) !== n;
  progressEl.textContent = `Step ${n} of ${steps.length}`;
  const heading = steps[n - 1].querySelector('h2');
  heading.tabIndex = -1;
  heading.focus({ preventScroll: true });
  window.scrollTo(0, 0);
  if (n === 4) renderSummary();
}

for (const button of wizard.querySelectorAll('[data-next]')) {
  button.addEventListener('click', () => {
    if (validate(step)) show(step + 1);
  });
}
for (const button of wizard.querySelectorAll('[data-back]')) {
  button.addEventListener('click', () => show(step - 1));
}

/** Whether the current step's answers are good enough to move on. Says why not. */
function validate(n) {
  if (n === 1) return validateStations();
  if (n === 2) return keyReady();
  return true;
}

/* --------------------------------------------------------------- stations */

function cards() {
  return Array.from(stationsEl.querySelectorAll('.station'));
}

function field(card, name) {
  return card.querySelector(`[data-station="${name}"]`);
}

function stationCard(station = {}) {
  const card = stationTemplate.content.firstElementChild.cloneNode(true);
  field(card, 'crs').value = station.crs || '';
  // Radios only exclude one another inside a group, and this is the one place a
  // name is wanted: a per-card group, which nothing reads back as a field.
  const group = `mode-${(radioGroups += 1)}`;
  for (const radio of card.querySelectorAll('[data-station="mode"]')) {
    radio.name = group;
    radio.checked = radio.value === (station.mode || 'departures');
  }
  card.querySelector('[data-remove]').addEventListener('click', () => {
    card.remove();
    renumber();
  });
  wireStationLookup({
    lookup: field(card, 'lookup'),
    crsField: field(card, 'crs'),
    listbox: card.querySelector('.suggestions'),
    note: card.querySelector('.crs-note'),
  });
  // Picking or typing a station clears what was said about it.
  field(card, 'crs').addEventListener('input', () => {
    const error = field(card, 'error');
    error.hidden = true;
    testedKey = null;
    keyResultEl.hidden = true;
    syncKeyStep();
  });
  return card;
}

function renumber() {
  const list = cards();
  list.forEach((card, index) => {
    card.querySelector('.station-title').textContent = `Station ${index + 1}`;
    card.querySelector('[data-remove]').hidden = index === 0;
  });
  addStationButton.hidden = list.length >= 2;
}

function addStation(station) {
  stationsEl.append(stationCard(station));
  renumber();
}

addStationButton.addEventListener('click', () => {
  addStation();
  const last = cards().at(-1);
  field(last, 'lookup').focus();
});

function readStations() {
  return cards().map((card) => ({
    crs: field(card, 'crs').value.trim().toUpperCase(),
    mode: card.querySelector('[data-station="mode"]:checked')?.value || 'departures',
  }));
}

function flagStation(card, message) {
  const error = field(card, 'error');
  error.textContent = message;
  error.hidden = false;
  field(card, 'lookup').focus();
}

function validateStations() {
  const list = readStations();
  const seen = new Set();
  for (const [index, station] of list.entries()) {
    const card = cards()[index];
    if (!/^[A-Z]{3}$/.test(station.crs)) {
      flagStation(card, 'Pick a station from the list.');
      return false;
    }
    const id = `${station.crs}/${station.mode}`;
    if (seen.has(id)) {
      flagStation(card, 'That is the same as the first station: pick another, or change what it shows.');
      return false;
    }
    seen.add(id);
  }
  return true;
}

/* -------------------------------------------------------------------- key */

const enteredKey = () => keyEl.value.trim();

/** A blank box is fine only when a key is already saved; a typed one must have passed. */
function keyReady() {
  const typed = enteredKey();
  if (!typed) return keySaved;
  return typed === testedKey;
}

function syncKeyStep() {
  keyNextButton.disabled = !keyReady();
  testKeyButton.disabled = !enteredKey();
}

function say(el, text, kind) {
  el.textContent = text;
  el.hidden = !text;
  el.classList.remove('ok', 'warn', 'error');
  if (kind) el.classList.add(kind);
}

keyEl.addEventListener('input', () => {
  testedKey = null;
  say(keyResultEl, '');
  say(keyErrorEl, '');
  syncKeyStep();
});

// Enter in the box means "test it", never "submit the page".
keyEl.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  if (enteredKey()) testKey();
});
testKeyButton.addEventListener('click', testKey);

/** The next train, worded for a person. */
function describeTrain(train) {
  if (!train) return 'No trains are listed just now, which is fine.';
  const platform = train.platform ? `, platform ${train.platform}` : '';
  const late = train.expected && /^\d\d:\d\d$/.test(train.expected) && train.expected !== train.time
    ? ` (expected ${train.expected})`
    : '';
  return `The next train is the ${train.time} to ${train.destination}${platform}${late}.`;
}

/** A pydantic message without its "Value error, " lead-in. */
function plain(message) {
  return String(message || '').replace(/^Value error, /, '');
}

async function testKey() {
  const key = enteredKey();
  if (!key) {
    say(keyErrorEl, 'Paste your key first.', 'error');
    return;
  }
  const [station] = readStations();
  testKeyButton.disabled = true;
  testKeyButton.textContent = 'Testing…';
  say(keyErrorEl, '');
  say(keyResultEl, '');
  try {
    const response = await fetch('/api/setup/test-key', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ key, crs: station.crs, mode: station.mode }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      say(keyErrorEl, plain(body.detail?.[0]?.msg) || `The board could not test the key (${response.status}).`, 'error');
    } else if (body.result === 'ok') {
      testedKey = key;
      say(keyResultEl, `Your key works. ${body.station}. ${describeTrain(body.next_train)}`, 'ok');
    } else if (body.result === 'rejected') {
      // TODO(graham): confirm wording. RDM's 401 and 403 cannot be told apart from
      // here (nothing in the repo records what it answers for a valid key with no
      // subscription to this product), so the message names both causes.
      say(
        keyResultEl,
        'The Rail Data Marketplace did not accept this key. Check that you copied all of the '
          + 'Consumer key, and that you subscribed to Live Arrival and Departure Boards, '
          + 'not the departures-only product.',
        'error',
      );
    } else {
      say(
        keyResultEl,
        'The board could not reach the Rail Data Marketplace'
          + `${body.detail ? ` (${body.detail})` : ''}. Check that the Pi is online, and try again.`,
        'warn',
      );
    }
  } catch {
    say(keyResultEl, 'The board did not answer. Check that you are still on the same Wi-Fi.', 'warn');
  } finally {
    testKeyButton.textContent = 'Test key';
    syncKeyStep();
  }
}

/* ------------------------------------------------------------------ sound */

function syncSound() {
  const on = announceEl.checked;
  soundOptionsEl.toggleAttribute('data-off', !on);
  soundOptionsEl.inert = !on;
}
announceEl.addEventListener('change', syncSound);

playTestButton.addEventListener('click', async () => {
  playTestButton.disabled = true;
  say(soundResultEl, '');
  try {
    // The output on screen, not the saved one: nothing is saved until Finish.
    const response = await fetch('/api/announce/test', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        text: 'This is a test of your departure board.',
        audio_device: deviceEl.value,
      }),
    });
    if (response.ok) {
      const where = DEVICE_TEXT[deviceEl.value] || 'the speakers';
      say(soundResultEl, `You should have heard an announcement through ${where}. If not, try the other output.`, 'ok');
    } else {
      const body = await response.json().catch(() => ({}));
      say(soundResultEl, `The board could not play the test: ${typeof body.detail === 'string' ? body.detail : response.statusText}.`, 'warn');
    }
  } catch {
    say(soundResultEl, 'The board did not answer.', 'warn');
  } finally {
    playTestButton.disabled = false;
  }
});

/* ----------------------------------------------------------------- finish */

function renderSummary() {
  summaryEl.textContent = '';
  const row = (term, description) => {
    const dt = document.createElement('dt');
    dt.textContent = term;
    const dd = document.createElement('dd');
    dd.textContent = description;
    summaryEl.append(dt, dd);
  };
  readStations().forEach((station, index) => {
    row(
      index === 0 ? 'Station' : 'And',
      `${stationNames.get(station.crs) || station.crs}, ${MODE_TEXT[station.mode]}`,
    );
  });
  row('Rail data key', enteredKey() ? 'Tested and ready to save' : 'Already saved');
  row('Sound', announceEl.checked ? `On, through ${DEVICE_TEXT[deviceEl.value]}` : 'Off');
}

/** Where a server complaint belongs on this page. */
function placeErrors(detail) {
  if (typeof detail === 'string') return say(finishErrorEl, detail, 'error');
  let target = 4;
  const unplaced = [];
  for (const error of Array.isArray(detail) ? detail : []) {
    const path = (error.loc || []).filter((part) => part !== 'body');
    const message = plain(error.msg);
    if (path[0] === 'key') {
      say(keyErrorEl, message, 'error');
      target = Math.min(target, 2);
    } else if (path[0] === 'stations') {
      const card = typeof path[1] === 'number' ? cards()[path[1]] : cards().at(-1);
      // The pattern's own message is regex; the person needs "pick a station".
      flagStation(card, path[2] === 'crs' ? 'Pick a station from the list.' : message);
      target = Math.min(target, 1);
    } else {
      unplaced.push(message);
    }
  }
  say(finishErrorEl, unplaced.join(' '), 'error');
  if (target !== 4) show(target);
  return undefined;
}

wizard.addEventListener('submit', (event) => {
  event.preventDefault();
  if (step === 4) finish();
});

async function finish() {
  finishButton.disabled = true;
  finishButton.textContent = 'Saving…';
  say(finishErrorEl, '');
  try {
    const response = await fetch('/api/setup/complete', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        stations: readStations(),
        key: enteredKey() || null,
        announce: announceEl.checked,
        audio_device: deviceEl.value,
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      placeErrors(body.detail || `The board could not save this (${response.status}).`);
      return;
    }
    keyEl.value = '';
    testedKey = null;
    for (const section of steps) section.hidden = true;
    progressEl.hidden = true;
    doneEl.hidden = false;
    doneEl.querySelector('h2').tabIndex = -1;
    doneEl.querySelector('h2').focus({ preventScroll: true });
    window.scrollTo(0, 0);
  } catch {
    say(finishErrorEl, 'The board did not answer. Check that you are still on the same Wi-Fi, then try again.', 'error');
  } finally {
    finishButton.disabled = false;
    finishButton.textContent = 'Finish';
  }
}

/* ------------------------------------------------------------------ start */

async function start() {
  const [config, creds] = await Promise.all([
    fetch('/api/config').then((r) => (r.ok ? r.json() : null)).catch(() => null),
    fetch('/api/credentials').then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);
  keySaved = creds?.keys?.rdm?.set === true;
  keySavedEl.hidden = !keySaved;
  keyEl.placeholder = keySaved ? 'Unchanged' : 'Paste your key';

  // A fresh Pi has whatever station the example config names; making the person
  // choose is better than letting Paddington through by default. Once a key is
  // saved the page shows what is set, so a second visit edits rather than resets.
  const known = keySaved ? config?.stations || [] : [];
  for (const station of known.length ? known : [{}]) addStation(station);

  const sound = config?.announcements;
  if (sound) {
    announceEl.checked = sound.enabled !== false;
    if (sound.audio_device && ![...deviceEl.options].some((o) => o.value === sound.audio_device)) {
      deviceEl.add(new Option('The default output', sound.audio_device));
    }
    deviceEl.value = sound.audio_device || 'hdmi';
  }
  syncSound();
  syncKeyStep();
  show(1);
}

start();
