/** Admin page: edits the same config.yaml the board reads. LAN-only, no login. */

const form = document.getElementById('config-form');
const stationsEl = document.getElementById('stations');
const weekdaysEl = document.getElementById('weekdays');
const messageEl = document.getElementById('message');
const statusList = document.getElementById('status-list');

const DAYS = [
  ['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'],
  ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday'],
];

let config = null;

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

/* ------------------------------------------------------------- stations */

function stationCard(station, index) {
  const card = document.createElement('div');
  card.className = 'station';
  card.innerHTML = `
    <div class="station-head">
      <strong>Station ${index + 1}</strong>
      <button type="button" class="danger" data-remove="${index}">Remove</button>
    </div>
    <div class="grid">
      <label>CRS code
        <input type="text" data-station="crs" maxlength="3" pattern="[A-Za-z]{3}" required>
      </label>
      <label>Mode
        <select data-station="mode">
          <option value="departures">Departures</option>
          <option value="arrivals">Arrivals</option>
        </select>
      </label>
      <label>Name override
        <input type="text" data-station="name" placeholder="(use the API's name)">
      </label>
      <label>Rows
        <input type="number" data-station="rows" min="1" max="20" step="1">
      </label>
      <label class="check"><input type="checkbox" data-station="announce"> Announcements</label>
    </div>`;

  card.querySelector('[data-station="crs"]').value = station.crs || '';
  card.querySelector('[data-station="mode"]').value = station.mode || 'departures';
  card.querySelector('[data-station="name"]').value = station.name || '';
  card.querySelector('[data-station="rows"]').value = station.rows ?? 8;
  card.querySelector('[data-station="announce"]').checked = station.announce !== false;

  card.querySelector('[data-remove]').addEventListener('click', () => {
    if (config.stations.length === 1) {
      showMessage('At least one station is required.', false);
      return;
    }
    config.stations = readStations();
    config.stations.splice(index, 1);
    renderStations();
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
    const name = card.querySelector('[data-station="name"]').value.trim();
    return {
      crs: card.querySelector('[data-station="crs"]').value.trim().toUpperCase(),
      mode: card.querySelector('[data-station="mode"]').value,
      name: name || null,
      rows: Number(card.querySelector('[data-station="rows"]').value),
      announce: card.querySelector('[data-station="announce"]').checked,
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
      <label class="check"><input type="checkbox" data-day="${key}" data-role="off"> Off all day</label>
      <div class="times">
        <input type="time" data-day="${key}" data-role="on_time">
        <input type="time" data-day="${key}" data-role="off_time">
      </div>`;
    box.querySelector('[data-role="active"]').checked = active;
    box.querySelector('[data-role="off"]').checked = offAllDay;
    box.querySelector('[data-role="on_time"]').value = override?.on_time || config.schedule.on_time;
    box.querySelector('[data-role="off_time"]').value = override?.off_time || config.schedule.off_time;
    weekdaysEl.append(box);
  }
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

/* ----------------------------------------------------------------- form */

function fillForm() {
  for (const field of form.querySelectorAll('[name]')) {
    const value = get(config, field.name);
    if (value === undefined) continue;
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else field.value = value ?? '';
  }
  renderStations();
  renderWeekdays();
}

function readForm() {
  const draft = structuredClone(config);
  for (const field of form.querySelectorAll('[name]')) {
    let value;
    if (field.type === 'checkbox') value = field.checked;
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

function showMessage(text, ok) {
  messageEl.textContent = text;
  messageEl.className = `message ${ok ? 'ok' : 'bad'}`;
  if (ok) setTimeout(() => { messageEl.textContent = ''; }, 4000);
}

function formatErrors(detail) {
  if (!Array.isArray(detail)) return String(detail);
  return detail.map((e) => `${(e.loc || []).join('.')}: ${e.msg}`).join('\n');
}

/* --------------------------------------------------------------- status */

function pill(ok, label) {
  return `<span class="pill ${ok ? 'ok' : 'bad'}">${label}</span>`;
}

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
    .map(([period, left]) => pill(left > 10, `${left} left this ${period}`))
    .join(' ');
}

async function loadStatus() {
  try {
    const status = await (await fetch('/api/status')).json();
    const boards = status.boards
      .map((b) => `${b.crs} ${b.mode} — ${b.services} services ${b.stale ? pill(false, 'stale') : pill(true, 'live')}${b.source ? ` ${pill(true, sourceLabel(b.source))}` : ''}`)
      .join('<br>');
    const health = [
      pill(status.primary_healthy, `primary: ${sourceLabel(status.primary)}`),
      status.fallback ? pill(status.fallback_healthy, `fallback: ${sourceLabel(status.fallback)}`) : pill(true, 'no fallback'),
    ].join(' ');
    statusList.innerHTML = `
      <dt>Active source</dt><dd>${pill(true, sourceLabel(status.active_source))}
        ${status.forced_source === 'auto' ? '' : pill(false, `forced: ${status.forced_source}`)}</dd>
      <dt>Sources</dt><dd>${health}</dd>
      <dt>Credentials</dt><dd>${pill(status.credentials.rdm, `RDM_API_KEY ${status.credentials.rdm ? 'present' : 'missing'}`)}
        ${pill(status.credentials.rtt, `RTT_TOKEN ${status.credentials.rtt ? 'present' : 'missing'}`)}</dd>
      <dt>Allowance</dt><dd>${formatAllowance(status.rate_limit)}</dd>
      <dt>Last fetch</dt><dd>${formatTime(status.last_fetch)}</dd>
      <dt>Last error</dt><dd>${status.last_error ? pill(false, status.last_error) : pill(true, 'none')}</dd>
      <dt>Display</dt><dd>${status.display_on ? pill(true, 'on') : pill(false, 'off (schedule)')}</dd>
      <dt>Piper</dt><dd>${pill(status.tts.piper, status.tts.piper ? 'found' : 'not found')}
        ${pill(status.tts.voice, status.tts.voice ? 'voice ok' : 'voice missing')}
        ${pill(Boolean(status.tts.player), status.tts.player || 'no player')}</dd>
      <dt>Last announcement</dt><dd>${status.last_announcement || '—'}</dd>
      <dt>Boards</dt><dd>${boards || '—'}</dd>
      <dt>Config file</dt><dd><code>${status.config_path}</code></dd>`;
    document.getElementById('force-source').value = status.forced_source || 'auto';
  } catch (err) {
    statusList.innerHTML = `<dt>Status</dt><dd>${pill(false, 'unavailable')}</dd>`;
  }
}

/* ---------------------------------------------------------------- wiring */

async function loadConfig() {
  const response = await fetch('/api/config');
  config = await response.json();
  fillForm();
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const draft = readForm();
  const response = await fetch('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(draft),
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
});

document.getElementById('add-station').addEventListener('click', () => {
  config.stations = readStations();
  if (config.stations.length >= 2) return;
  const first = config.stations[0];
  config.stations.push({ crs: first.crs, mode: 'arrivals', name: null, rows: first.rows, announce: false });
  renderStations();
});

document.getElementById('reload').addEventListener('click', () => {
  loadConfig().then(() => showMessage('Reloaded from disk.', true));
});

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

loadConfig();
loadStatus();
setInterval(loadStatus, 15000);
