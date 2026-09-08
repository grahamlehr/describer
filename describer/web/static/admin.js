/** Admin page: edits the same config.yaml the board reads. LAN-only, no login. */

const form = document.getElementById('config-form');
const stationsEl = document.getElementById('stations');
const weekdaysEl = document.getElementById('weekdays');
const messageEl = document.getElementById('message');
const statusList = document.getElementById('status-list');
const saveButton = document.getElementById('save');
const stationTemplate = document.getElementById('station-template');

const DAYS = [
  ['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'],
  ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday'],
];

let config = null;
let dirty = false;

/* --------------------------------------------------------- path helpers */

function get(object, path) {
  return path.split('.').reduce((node, key) => (node == null ? node : node[key]), object);
}

function set(object, path, value) {
  const keys = path.split('.');
  const last = keys.pop();
  const node = keys.reduce((current, key) => (current[key] ??= {}), object);
  node[last] = value;
}

/* ----------------------------------------------------------------- tabs */

const tabs = document.getElementById('tabs');
const panels = document.querySelectorAll('[data-panel]');

function showTab(name) {
  for (const button of tabs.querySelectorAll('[data-tab]')) {
    button.setAttribute('aria-selected', String(button.dataset.tab === name));
  }
  for (const panel of panels) panel.hidden = panel.dataset.panel !== name;
  // The tab is in the URL so a reload, or a link sent to yourself, lands here.
  if (location.hash.slice(1) !== name) history.replaceState(null, '', `#${name}`);
}

tabs.addEventListener('click', (event) => {
  const button = event.target.closest('[data-tab]');
  if (button) showTab(button.dataset.tab);
});

/** Reveal whichever tab holds an element, so validation errors are never hidden. */
function revealTabFor(element) {
  const panel = element.closest('[data-panel]');
  if (panel && panel.hidden) showTab(panel.dataset.panel);
}

/* --------------------------------------------------- progressive disclosure */

/** Only the selected theme's options are worth showing; the rest are noise. */
function syncThemeOptions() {
  const theme = form.querySelector('[name="display.theme"]:checked')?.value;
  for (const block of document.querySelectorAll('.theme-opts')) {
    block.hidden = block.dataset.theme !== theme;
  }
}

/** A gated block is inert and dimmed while its feature is switched off. */
function syncGates() {
  for (const toggle of form.querySelectorAll('[data-gate]')) {
    const block = document.getElementById(`${toggle.dataset.gate}-gate`);
    if (!block) continue;
    block.toggleAttribute('data-off', !toggle.checked);
    block.inert = !toggle.checked;
  }
}

/* ------------------------------------------------------------- stations */

function stationCard(station, index) {
  const card = stationTemplate.content.firstElementChild.cloneNode(true);
  const field = (name) => card.querySelector(`[data-station="${name}"]`);

  card.querySelector('.station-title').textContent = `Station ${index + 1}`;
  field('crs').value = station.crs || '';
  field('mode').value = station.mode || 'departures';
  field('name').value = station.name || '';
  field('rows').value = station.rows ?? 8;
  field('announce').checked = station.announce !== false;
  field('platforms').value = (station.platforms || []).join(', ');
  field('show_unplatformed').checked = station.show_unplatformed === true;

  card.querySelector('[data-remove]').addEventListener('click', () => {
    if (config.stations.length === 1) {
      showMessage('At least one station is required.', false);
      return;
    }
    config.stations = readStations();
    config.stations.splice(index, 1);
    renderStations();
    markDirty();
  });
  return card;
}

function renderStations() {
  stationsEl.textContent = '';
  config.stations.forEach((station, index) => stationsEl.append(stationCard(station, index)));
  document.getElementById('add-station').disabled = config.stations.length >= 2;
}

function readStations() {
  return Array.from(stationsEl.querySelectorAll('.station')).map((card) => {
    const field = (name) => card.querySelector(`[data-station="${name}"]`);
    const name = field('name').value.trim();
    return {
      crs: field('crs').value.trim().toUpperCase(),
      mode: field('mode').value,
      name: name || null,
      rows: Number(field('rows').value),
      announce: field('announce').checked,
      platforms: field('platforms')
        .value.split(',')
        .map((entry) => entry.trim().toUpperCase())
        .filter(Boolean),
      show_unplatformed: field('show_unplatformed').checked,
    };
  });
}

/* ------------------------------------------------------------- weekdays */

function renderWeekdays() {
  weekdaysEl.textContent = '';
  const overrides = config.schedule.per_weekday || {};
  for (const [key, label] of DAYS) {
    const override = overrides[key];
    const active = key in overrides;
    const offAllDay = active && override === null;
    const box = document.createElement('div');
    box.className = 'weekday';
    box.innerHTML = `
      <label class="check"><input type="checkbox" data-day="${key}" data-role="active"> ${label}</label>
      <label class="check off-toggle"><input type="checkbox" data-day="${key}" data-role="off"> Off all day</label>
      <div class="times">
        <input type="time" data-day="${key}" data-role="on_time" aria-label="${label} on at">
        <input type="time" data-day="${key}" data-role="off_time" aria-label="${label} off at">
      </div>`;
    box.querySelector('[data-role="active"]').checked = active;
    box.querySelector('[data-role="off"]').checked = offAllDay;
    box.querySelector('[data-role="on_time"]').value = override?.on_time || config.schedule.on_time;
    box.querySelector('[data-role="off_time"]').value = override?.off_time || config.schedule.off_time;
    box.addEventListener('change', () => syncWeekday(box));
    weekdaysEl.append(box);
    syncWeekday(box);
  }
}

/** Dim the controls a day is not currently using, rather than hiding them. */
function syncWeekday(box) {
  const active = box.querySelector('[data-role="active"]').checked;
  const off = box.querySelector('[data-role="off"]').checked;
  box.toggleAttribute('data-inactive', !active);
  box.toggleAttribute('data-off', off);
}

function readWeekdays() {
  const result = {};
  for (const box of weekdaysEl.querySelectorAll('.weekday')) {
    const active = box.querySelector('[data-role="active"]');
    if (!active.checked) continue;
    const day = active.dataset.day;
    if (box.querySelector('[data-role="off"]').checked) {
      result[day] = null;
    } else {
      result[day] = {
        on_time: box.querySelector('[data-role="on_time"]').value,
        off_time: box.querySelector('[data-role="off_time"]').value,
      };
    }
  }
  return result;
}

/* -------------------------------------------------------------- colours */

/** The shared palette vocabulary, in the order the admin page shows it. */
const COLOUR_ROLES = [
  ['background', 'Background'],
  ['text', 'Text'],
  ['dim_text', 'Dim text'],
  ['accent', 'Accent'],
  ['on_time', 'On time'],
  ['late', 'Late'],
  ['cancelled', 'Cancelled'],
];

/** Which roles each theme actually has. thameslink counts down in the text
 *  colour, so it has no on-time colour to offer. */
const THEME_ROLES = {
  modern: COLOUR_ROLES.map(([role]) => role),
  thameslink: COLOUR_ROLES.map(([role]) => role).filter((role) => role !== 'on_time'),
};

/** Role → the custom property the stylesheets name it by. Kept in step with
 *  themes/colours.js, which is what writes them on the board. */
const ROLE_PROPERTY = {
  background: '--bg',
  text: '--fg',
  dim_text: '--muted',
  accent: '--accent',
  on_time: '--on-time',
  late: '--late',
  cancelled: '--cancelled',
};

/** Each theme's own colours, read from its stylesheet rather than copied
 *  here, so this page cannot drift from the board. */
const defaults = {};

const FALLBACK_COLOUR = '#000000';

/** "#abc" and "#aabbcc" both reach the picker as "#aabbcc"; anything else
 *  (a color-mix, a named colour) has no swatch and is left to the theme. */
function normaliseHex(value) {
  const text = String(value || '').trim();
  if (/^#[0-9a-fA-F]{6}$/.test(text)) return text.toLowerCase();
  if (/^#[0-9a-fA-F]{3}$/.test(text)) {
    return ('#' + text[1] + text[1] + text[2] + text[2] + text[3] + text[3]).toLowerCase();
  }
  return null;
}

async function loadDefaults(theme) {
  const found = {};
  try {
    const css = await (await fetch(`/static/themes/${theme}.css`)).text();
    const root = css.match(/:root\s*\{([^}]*)\}/);
    for (const [role, property] of Object.entries(ROLE_PROPERTY)) {
      const match = (root ? root[1] : '').match(new RegExp(`${property}\\s*:\\s*([^;]+);`));
      const hex = match ? normaliseHex(match[1]) : null;
      if (hex) found[role] = hex;
    }
  } catch (err) {
    // A stylesheet we cannot read costs the swatches, not the page.
  }
  defaults[theme] = found;
}

function colourRow(theme, role, label) {
  const row = document.createElement('div');
  row.className = 'colour';
  row.dataset.role = role;
  row.innerHTML = `
    <input type="color" name="display.themes.${theme}.colours.${role}"
           data-colour="${theme}" data-role="${role}" aria-label="${label}">
    <span class="colour-name">${label}</span>
    <span class="ratio" data-ratio></span>
    <button type="button" class="linky" data-reset>Default</button>`;
  row.querySelector('[data-reset]').addEventListener('click', () => {
    const field = row.querySelector('input');
    field.dataset.unset = '1';
    field.value = defaults[theme]?.[role] || FALLBACK_COLOUR;
    markDirty();
    syncColours();
  });
  return row;
}

function buildColourFields() {
  for (const [theme, roles] of Object.entries(THEME_ROLES)) {
    const host = document.querySelector(`[data-colours="${theme}"]`);
    if (!host) continue;
    for (const [role, label] of COLOUR_ROLES) {
      if (roles.includes(role)) host.append(colourRow(theme, role, label));
    }
  }
}

/** A colour field carries either a value or "use the theme's own". */
function setColourField(field, value) {
  const hex = normaliseHex(value);
  field.dataset.unset = hex ? '0' : '1';
  field.value = hex || defaults[field.dataset.colour]?.[field.dataset.role] || FALLBACK_COLOUR;
}

/** What the board would actually paint: the override, or the theme's own. */
function effectiveColours(theme) {
  const result = { ...(defaults[theme] || {}) };
  for (const field of document.querySelectorAll(`[data-colour="${theme}"]`)) {
    if (field.dataset.unset !== '1') result[field.dataset.role] = field.value;
  }
  return result;
}

/* WCAG relative luminance, so the warning below matches what a contrast
   checker would say. A board read from across a platform needs the margin. */
function luminance(hex) {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
}

const MIN_CONTRAST = 4.5;

/** Every colour on the board is read against the ground, the status colours
 *  included: an amber that vanishes on a pale background is the mistake this
 *  is here to catch. */
function syncColours() {
  for (const theme of Object.keys(THEME_ROLES)) {
    const host = document.querySelector(`[data-colours="${theme}"]`);
    if (!host) continue;
    const colours = effectiveColours(theme);
    const ground = colours.background;
    let worst = null;
    for (const row of host.querySelectorAll('.colour')) {
      const role = row.dataset.role;
      const cell = row.querySelector('[data-ratio]');
      const colour = colours[role];
      if (role === 'background' || !ground || !colour) {
        cell.textContent = '';
        cell.removeAttribute('data-low');
        continue;
      }
      const ratio = contrast(colour, ground);
      cell.textContent = `${ratio.toFixed(1)}:1`;
      cell.toggleAttribute('data-low', ratio < MIN_CONTRAST);
      if (ratio < MIN_CONTRAST && (worst === null || ratio < worst)) worst = ratio;
    }
    renderPreview(theme, colours, worst);
  }
}

/** A board row in the chosen colours, so a palette is judged before it is
 *  saved rather than on a walk to the monitor. */
function renderPreview(theme, colours, worst) {
  const host = document.querySelector(`[data-preview="${theme}"]`);
  if (!host) return;
  const c = (role, fallback) => colours[role] || fallback || 'currentColor';
  const rows = [
    ['14:32', 'London Charing Cross', '2', 'On time', c('on_time', c('text'))],
    ['14:41', 'Abbey Wood via Whitechapel', '1', 'Exp 14:52', c('late')],
    ['14:56', 'Ashford International', '—', 'Cancelled', c('cancelled')],
  ];
  host.innerHTML = `
    <div class="preview-board" style="background:${c('background')};color:${c('text')}">
      <div class="preview-head" style="color:${c('accent')}">New Beckenham · Departures</div>
      ${rows.map(([time, dest, plat, status, colour]) => `
        <div class="preview-row">
          <span>${time}</span><span class="preview-dest">${dest}</span>
          <span style="color:${c('dim_text')}">${plat}</span>
          <span style="color:${colour}">${status}</span>
        </div>`).join('')}
    </div>
    ${worst === null ? '' : `<p class="warn-line">Lowest contrast ${worst.toFixed(1)}:1 — under ${MIN_CONTRAST}:1 this is hard to read across a platform. It will still save.</p>`}`;
}

/* ----------------------------------------------------------------- form */

function fillForm() {
  for (const field of form.querySelectorAll('[name]')) {
    const value = get(config, field.name);
    if (value === undefined) continue;
    if (field.type === 'color') setColourField(field, value);
    else if (field.type === 'radio') field.checked = field.value === String(value);
    else if (field.type === 'checkbox') field.checked = Boolean(value);
    else field.value = value ?? '';
  }
  renderStations();
  renderWeekdays();
  syncThemeOptions();
  syncColours();
  syncGates();
  markClean();
}

function readForm() {
  const draft = structuredClone(config);
  for (const field of form.querySelectorAll('[name]')) {
    let value;
    if (field.type === 'radio') {
      if (!field.checked) continue;
      value = field.value;
    } else if (field.type === 'color') value = field.dataset.unset === '1' ? null : field.value;
    else if (field.type === 'checkbox') value = field.checked;
    else if (field.type === 'number' || field.type === 'range') value = Number(field.value);
    else value = field.value;
    set(draft, field.name, value);
  }
  draft.stations = readStations();
  draft.schedule.per_weekday = readWeekdays();
  // "None" on the fallback select means no failover at all.
  if (draft.sources.fallback === '') draft.sources.fallback = null;
  return draft;
}

function markDirty() {
  dirty = true;
  saveButton.dataset.dirty = 'true';
  saveButton.title = 'Unsaved changes';
}

function markClean() {
  dirty = false;
  delete saveButton.dataset.dirty;
  saveButton.title = '';
}

function showMessage(text, ok) {
  messageEl.textContent = text.split('\n')[0];
  messageEl.title = text;
  messageEl.className = `message ${ok ? 'ok' : 'bad'}`;
  if (ok) setTimeout(() => { messageEl.textContent = ''; messageEl.title = ''; }, 4000);
}

function formatErrors(detail) {
  if (!Array.isArray(detail)) return String(detail);
  return detail.map((e) => `${(e.loc || []).join('.')}: ${e.msg}`).join('\n');
}

/* --------------------------------------------------------------- status */

function pill(kind, label) {
  return `<span class="pill ${kind}">${label}</span>`;
}

const ok = (label) => pill('ok', label);
const bad = (label) => pill('bad', label);
const flag = (good, label) => pill(good ? 'ok' : 'bad', label);

function formatTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString('en-GB') : 'never';
}

const SOURCE_LABELS = { rdm: 'Rail Data Marketplace', rtt: 'Realtime Trains' };

function sourceLabel(name) {
  return name ? SOURCE_LABELS[name] || name : 'none';
}

function formatAllowance(limits) {
  const entries = Object.entries(limits || {});
  if (!entries.length) return '—';
  // The free RTT tier is metered; a board that runs dry stops updating.
  return entries
    .map(([period, left]) => pill(left > 10 ? 'ok' : 'warn', `${left} left this ${period}`))
    .join(' ');
}

const MODE_LABELS = { auto: 'monitor default', '1080p': '1920×1080', '720p': '1280×720' };

function formatMode(mode) {
  // null means the last resolution change has not reached the compositor yet.
  if (!mode) return bad('resolution pending');
  return ok(MODE_LABELS[mode] || mode);
}

/** One line in the top bar that says whether the board is actually working. */
function setGlance(kind, text) {
  document.getElementById('glance-dot').className = `dot ${kind}`;
  document.getElementById('glance-text').textContent = text;
}

async function loadStatus() {
  try {
    const status = await (await fetch('/api/status')).json();
    const boards = status.boards
      .map((b) => `${b.crs} ${b.mode} — ${b.services} services ${b.stale ? bad('stale') : ok('live')}${b.source ? ` ${ok(sourceLabel(b.source))}` : ''}`)
      .join('<br>');
    const health = [
      flag(status.primary_healthy, `primary: ${sourceLabel(status.primary)}`),
      status.fallback ? flag(status.fallback_healthy, `fallback: ${sourceLabel(status.fallback)}`) : pill('', 'no fallback'),
    ].join(' ');
    statusList.innerHTML = `
      <dt>Active source</dt><dd>${ok(sourceLabel(status.active_source))}
        ${status.forced_source === 'auto' ? '' : bad(`forced: ${status.forced_source}`)}</dd>
      <dt>Sources</dt><dd>${health}</dd>
      <dt>Credentials</dt><dd>${flag(status.credentials.rdm, `RDM_API_KEY ${status.credentials.rdm ? 'present' : 'missing'}`)}
        ${flag(status.credentials.rtt, `RTT_TOKEN ${status.credentials.rtt ? 'present' : 'missing'}`)}</dd>
      <dt>Allowance</dt><dd>${formatAllowance(status.rate_limit)}</dd>
      <dt>Last fetch</dt><dd>${formatTime(status.last_fetch)}</dd>
      <dt>Last error</dt><dd>${status.last_error ? bad(status.last_error) : ok('none')}</dd>
      <dt>Display</dt><dd>${status.display_on ? ok('on') : bad('off (schedule)')}
        ${formatMode(status.display_mode)}</dd>
      <dt>Piper</dt><dd>${flag(status.tts.piper, status.tts.piper ? 'found' : 'not found')}
        ${flag(status.tts.voice, status.tts.voice ? 'voice ok' : 'voice missing')}
        ${flag(Boolean(status.tts.player), status.tts.player || 'no player')}</dd>
      <dt>Last announcement</dt><dd>${status.last_announcement || '—'}</dd>
      <dt>Boards</dt><dd>${boards || '—'}</dd>
      <dt>Config file</dt><dd><code>${status.config_path}</code></dd>`;
    document.getElementById('force-source').value = status.forced_source || 'auto';

    const stale = status.boards.some((b) => b.stale);
    const source = sourceLabel(status.active_source);
    if (status.last_error || !status.primary_healthy) {
      setGlance(stale ? 'bad' : 'warn', `${source} · ${stale ? 'stale' : 'recovering'}`);
    } else {
      setGlance(stale ? 'warn' : 'ok', `${source} · ${formatTime(status.last_fetch)}`);
    }
  } catch (err) {
    statusList.innerHTML = `<dt>Status</dt><dd>${bad('unavailable')}</dd>`;
    setGlance('bad', 'Backend unreachable');
  }
}

/* ---------------------------------------------------------------- wiring */

async function loadConfig() {
  buildColourFields();
  const response = await fetch('/api/config');
  config = await response.json();
  fillForm();
  // The theme stylesheets say what "Default" is; a slow read costs the
  // swatches for a moment, not the form.
  await Promise.all(Object.keys(THEME_ROLES).map(loadDefaults));
  for (const field of form.querySelectorAll('input[type="color"]')) {
    if (field.dataset.unset === '1') setColourField(field, null);
  }
  syncColours();
}

form.addEventListener('input', markDirty);
form.addEventListener('input', (event) => {
  if (event.target.type !== 'color') return;
  event.target.dataset.unset = '0';
  syncColours();
});
form.addEventListener('change', (event) => {
  markDirty();
  if (event.target.name === 'display.theme') syncThemeOptions();
  if (event.target.dataset.gate) syncGates();
});

// The form is novalidate so a field on a hidden tab cannot block submission
// silently; we find the first invalid field ourselves and open its tab.
function firstInvalid() {
  for (const field of form.querySelectorAll('input, select')) {
    if (!field.checkValidity()) return field;
  }
  return null;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const invalid = firstInvalid();
  if (invalid) {
    revealTabFor(invalid);
    invalid.reportValidity();
    showMessage('Some fields need fixing.', false);
    return;
  }
  saveButton.disabled = true;
  try {
    const response = await fetch('/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(readForm()),
    });
    if (response.ok) {
      config = await response.json();
      fillForm();
      showMessage('Saved and applied.', true);
      loadStatus();
    } else {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      showMessage(formatErrors(body.detail), false);
    }
  } finally {
    saveButton.disabled = false;
  }
});

document.getElementById('add-station').addEventListener('click', () => {
  config.stations = readStations();
  if (config.stations.length >= 2) return;
  const first = config.stations[0];
  config.stations.push({ crs: first.crs, mode: 'arrivals', name: null, rows: first.rows, announce: false });
  renderStations();
  markDirty();
});

document.getElementById('reload').addEventListener('click', () => {
  if (dirty && !confirm('Discard your unsaved changes?')) return;
  loadConfig().then(() => showMessage('Reloaded from disk.', true));
});

document.getElementById('glance').addEventListener('click', () => showTab('status'));

document.getElementById('apply-force').addEventListener('click', async () => {
  const source = document.getElementById('force-source').value;
  const response = await fetch('/api/source/force', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source }),
  });
  showMessage(response.ok ? `Source: ${source}.` : 'Could not change the source.', response.ok);
  loadStatus();
});

document.getElementById('test-announce').addEventListener('click', async () => {
  const text = document.getElementById('test-text').value.trim();
  const response = await fetch('/api/announce/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(text ? { text } : {}),
  });
  const body = await response.json().catch(() => ({}));
  showMessage(response.ok ? 'Announcement played.' : `Failed: ${body.detail || response.statusText}`, response.ok);
});

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === 's') {
    event.preventDefault();
    form.requestSubmit();
  }
});

window.addEventListener('beforeunload', (event) => {
  if (dirty) event.preventDefault();
});

showTab(document.querySelector(`[data-panel="${location.hash.slice(1)}"]`) ? location.hash.slice(1) : 'stations');
loadConfig();
loadStatus();
setInterval(loadStatus, 15000);
