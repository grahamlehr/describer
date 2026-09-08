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

function stationCard(station, index, onRemove) {
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
  field('walk_time').value = station.walk_time ?? 0;

  card.querySelector('[data-remove]').addEventListener('click', () => onRemove(index));
  return card;
}

/** A list of station cards in any host: the Stations tab, or one profile. */
function renderStationList(host, stations, onRemove) {
  host.textContent = '';
  stations.forEach((station, index) => host.append(stationCard(station, index, onRemove)));
}

function renderStations() {
  renderStationList(stationsEl, config.stations, (index) => {
    if (config.stations.length === 1) {
      showMessage('At least one station is required.', false);
      return;
    }
    config.stations = readStations();
    config.stations.splice(index, 1);
    renderStations();
    markDirty();
  });
  document.getElementById('add-station').disabled = config.stations.length >= 2;
}

function readStations() {
  return readStationList(stationsEl);
}

function readStationList(host) {
  return Array.from(host.querySelectorAll('.station')).map((card) => {
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
      walk_time: Number(field('walk_time').value) || 0,
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

/* -------------------------------------------------------------- profiles */

const profileListEl = document.getElementById('profile-list');
const profileEditorEl = document.getElementById('profile-editor');
const profileRibbonEl = document.getElementById('profile-ribbon');

const THEMES = [
  ['modern', 'Modern'], ['crt', 'CRT'], ['splitflap', 'Split-flap'],
  ['1990s', '1990s'], ['nse', 'Network SouthEast'], ['led-matrix', 'LED matrix'],
  ['thameslink', 'Thameslink'],
];

/** The options each theme offers, in the shape the Display tab spells out by
 *  hand. A new theme option belongs in both places.
 *
 *  Colours are deliberately absent: a palette belongs to a theme rather than
 *  to an hour, so a profile picking thameslink gets the thameslink palette from
 *  the Display tab. The config model will still carry one written by hand. */
const THEME_OPTIONS = {
  crt: [
    { key: 'phosphor', label: 'Phosphor', type: 'select', options: [['amber', 'Amber'], ['green', 'Green']] },
    { key: 'scanlines', label: 'Scanlines', type: 'check' },
    { key: 'curvature', label: 'Curvature and bloom', type: 'check' },
  ],
  splitflap: [
    { key: 'click_sound', label: 'Click sound', type: 'check' },
    { key: 'flap_ms', label: 'Flap speed', unit: 'ms per step', type: 'number', min: 10, max: 200 },
  ],
  nse: [
    { key: 'dot_colour', label: 'Dot colour', type: 'select', options: [['yellow', 'Yellow'], ['white', 'White'], ['green', 'Green']] },
    { key: 'click_sound', label: 'Rattle as discs flip', type: 'check' },
  ],
};

/** Enough hues to tell a handful of profiles apart on the ribbon. */
const PROFILE_HUES = [205, 30, 145, 280, 0, 55, 175, 320, 95, 250, 15, 130];

let selectedProfile = 0;

const entries = () => config.profiles?.entries || [];
const toMinutes = (hhmm) => {
  const [h, m] = String(hhmm || '00:00').split(':').map(Number);
  return h * 60 + m;
};

/* The board's own arithmetic, repeated here so the ribbon says what the Pi
   will do: start == end is all day, and a start after an end wraps midnight. */
function coversMinute(entry, day, minute) {
  if (!(entry.days || []).includes(day)) return false;
  const on = toMinutes(entry.start);
  const off = toMinutes(entry.end);
  if (on === off) return true;
  return on < off ? minute >= on && minute < off : minute >= on || minute < off;
}

/** Which profile owns this minute: the first one matching, or -1 for the base. */
function profileAt(day, minute) {
  return entries().findIndex((entry) => coversMinute(entry, day, minute));
}

function profileColour(index) {
  return index < 0 ? 'transparent' : `hsl(${PROFILE_HUES[index % PROFILE_HUES.length]} 62% 45%)`;
}

function profileLabel(entry, index) {
  return entry.name?.trim() || `Profile ${index + 1}`;
}

/* ------------------------------------------------------------- the list */

function renderProfileList() {
  profileListEl.textContent = '';
  entries().forEach((entry, index) => {
    const item = document.createElement('li');
    item.className = 'profile-item';
    item.toggleAttribute('data-selected', index === selectedProfile);
    item.innerHTML = `
      <button type="button" class="profile-pick" data-pick="${index}">
        <span class="profile-swatch" style="background:${profileColour(index)}"></span>
        <span class="profile-names">
          <strong>${escapeHtml(profileLabel(entry, index))}</strong>
          <small>${escapeHtml(entry.start)}&ndash;${escapeHtml(entry.end)} · ${daysLabel(entry.days)}</small>
        </span>
      </button>
      <span class="profile-order">
        <button type="button" class="linky" data-move="${index}" data-by="-1" title="Earlier in the order" ${index === 0 ? 'disabled' : ''}>&#9650;</button>
        <button type="button" class="linky" data-move="${index}" data-by="1" title="Later in the order" ${index === entries().length - 1 ? 'disabled' : ''}>&#9660;</button>
        <button type="button" class="linky danger" data-drop="${index}" title="Remove">&times;</button>
      </span>`;
    profileListEl.append(item);
  });
  if (!entries().length) {
    profileListEl.innerHTML = '<li class="profile-empty">No profiles. The board runs the other tabs all day.</li>';
  }
}

function daysLabel(days) {
  const chosen = days || [];
  if (chosen.length === 7) return 'every day';
  if (chosen.length === 5 && !chosen.includes('sat') && !chosen.includes('sun')) return 'weekdays';
  if (chosen.length === 2 && chosen.includes('sat') && chosen.includes('sun')) return 'weekends';
  if (!chosen.length) return 'no days';
  return chosen.map((day) => day[0].toUpperCase() + day.slice(1)).join(', ');
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"]/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]
  ));
}

/* ----------------------------------------------------------- the editor */

function optionField(spec, value) {
  const attrs = `data-option="${spec.key}"`;
  if (spec.type === 'check') {
    return `<label class="check"><input type="checkbox" ${attrs} ${value ? 'checked' : ''}> ${spec.label}</label>`;
  }
  if (spec.type === 'select') {
    const options = spec.options
      .map(([key, label]) => `<option value="${key}" ${key === value ? 'selected' : ''}>${label}</option>`)
      .join('');
    return `<label>${spec.label}<select ${attrs}>${options}</select></label>`;
  }
  return `<label>${spec.label} <span class="unit">${spec.unit || ''}</span>
    <input type="number" ${attrs} min="${spec.min}" max="${spec.max}" step="1" value="${value ?? ''}"></label>`;
}

function renderProfileEditor() {
  const entry = entries()[selectedProfile];
  if (!entry) {
    profileEditorEl.innerHTML = '<p class="hint">Add a profile to give an hour of the day its own board.</p>';
    return;
  }
  const display = entry.display || {};
  const theme = display.theme || config.display.theme;
  const options = { ...(config.display.themes?.[theme] || {}), ...(display.themes?.[theme] || {}) };
  const announcements = entry.announcements || {};
  const specs = THEME_OPTIONS[theme] || [];

  profileEditorEl.innerHTML = `
    <div class="grid">
      <label class="wide">Name
        <input type="text" data-profile="name" value="${escapeHtml(entry.name)}"
               maxlength="40" placeholder="Morning rush" required>
      </label>
      <label>From <input type="time" data-profile="start" value="${escapeHtml(entry.start)}"></label>
      <label>To <input type="time" data-profile="end" value="${escapeHtml(entry.end)}"></label>
    </div>
    <div class="days">${DAYS.map(([key, label]) => `
      <label class="check"><input type="checkbox" data-day="${key}"
        ${(entry.days || []).includes(key) ? 'checked' : ''}> ${label.slice(0, 3)}</label>`).join('')}
    </div>
    <p class="hint">The same time in both boxes means all day. A start later than
      an end wraps midnight.</p>

    <div class="override">
      <label class="switch"><input type="checkbox" data-profile="stations-on"
        ${entry.stations ? 'checked' : ''}> Different stations</label>
      <div class="override-body" data-body="stations" ${entry.stations ? '' : 'hidden'}>
        <div class="stations" data-profile-stations></div>
        <button type="button" class="ghost" data-add-station>Add station</button>
      </div>
    </div>

    <div class="override">
      <label class="switch"><input type="checkbox" data-profile="display-on"
        ${entry.display ? 'checked' : ''}> Different display</label>
      <div class="override-body" data-body="display" ${entry.display ? '' : 'hidden'}>
        <div class="grid">
          <label>Theme
            <select data-profile="theme">${THEMES.map(([key, label]) =>
              `<option value="${key}" ${key === theme ? 'selected' : ''}>${label}</option>`).join('')}</select>
          </label>
          <label class="check"><input type="checkbox" data-profile="clock"
            ${(display.clock ?? config.display.clock) ? 'checked' : ''}> Show clock</label>
          <label class="check"><input type="checkbox" data-profile="calling"
            ${(display.show_calling_points ?? config.display.show_calling_points) ? 'checked' : ''}> Show calling points</label>
        </div>
        ${specs.length ? `<div class="grid">${specs.map((spec) => optionField(spec, options[spec.key])).join('')}</div>` : ''}
        <p class="hint">Colours belong to the theme itself and are edited on the
          Display tab, so every hour showing this theme looks the same.</p>
      </div>
    </div>

    <div class="override">
      <label class="switch"><input type="checkbox" data-profile="announce-on"
        ${entry.announcements ? 'checked' : ''}> Different announcements</label>
      <div class="override-body" data-body="announcements" ${entry.announcements ? '' : 'hidden'}>
        <div class="grid">
          <label class="check"><input type="checkbox" data-profile="ann-enabled"
            ${(announcements.enabled ?? config.announcements.enabled) ? 'checked' : ''}> Announcements on</label>
          <label>Lead time <span class="unit">seconds</span>
            <input type="number" data-profile="lead_time" min="0" max="1800" step="10"
                   value="${announcements.lead_time ?? config.announcements.lead_time}"></label>
          <label>Volume
            <input type="range" data-profile="volume" min="0" max="1" step="0.05"
                   value="${announcements.volume ?? config.announcements.volume}"></label>
        </div>
        <div class="grid checks">
          <label class="check"><input type="checkbox" data-profile="chime"
            ${(announcements.chime ?? config.announcements.chime) ? 'checked' : ''}> Two-tone chime first</label>
          <label class="check"><input type="checkbox" data-profile="delays"
            ${(announcements.announce_delays ?? config.announcements.announce_delays) ? 'checked' : ''}> Announce delays</label>
          <label class="check"><input type="checkbox" data-profile="cancellations"
            ${(announcements.announce_cancellations ?? config.announcements.announce_cancellations) ? 'checked' : ''}> Announce cancellations</label>
        </div>
      </div>
    </div>`;

  if (entry.stations) renderProfileStations(entry);
}

function renderProfileStations(entry) {
  const host = profileEditorEl.querySelector('[data-profile-stations]');
  if (!host) return;
  renderStationList(host, entry.stations, (index) => {
    if (entry.stations.length === 1) {
      showMessage('A profile showing its own stations needs at least one.', false);
      return;
    }
    entry.stations = readStationList(host);
    entry.stations.splice(index, 1);
    renderProfileStations(entry);
    markDirty();
  });
  profileEditorEl.querySelector('[data-add-station]').disabled = entry.stations.length >= 2;
}

/** Read the open editor back into the draft. Called on every change, so the
 *  list, the ribbon and Save all see the same profile the user is editing. */
function commitProfileEditor() {
  const entry = entries()[selectedProfile];
  if (!entry || !profileEditorEl.querySelector('[data-profile="name"]')) return;
  const field = (name) => profileEditorEl.querySelector(`[data-profile="${name}"]`);

  entry.name = field('name').value;
  entry.start = field('start').value || '00:00';
  entry.end = field('end').value || '00:00';
  entry.days = DAYS
    .map(([key]) => key)
    .filter((key) => profileEditorEl.querySelector(`[data-day="${key}"]`).checked);

  const stationHost = profileEditorEl.querySelector('[data-profile-stations]');
  entry.stations = field('stations-on').checked ? readStationList(stationHost) : null;

  if (field('display-on').checked) {
    const theme = field('theme').value;
    const options = {};
    for (const input of profileEditorEl.querySelectorAll('[data-option]')) {
      options[input.dataset.option] =
        input.type === 'checkbox' ? input.checked
          : input.type === 'number' ? Number(input.value)
            : input.value;
    }
    entry.display = {
      theme,
      clock: field('clock').checked,
      show_calling_points: field('calling').checked,
    };
    if (Object.keys(options).length) entry.display.themes = { [theme]: options };
  } else {
    entry.display = null;
  }

  if (field('announce-on').checked) {
    entry.announcements = {
      enabled: field('ann-enabled').checked,
      lead_time: Number(field('lead_time').value),
      volume: Number(field('volume').value),
      chime: field('chime').checked,
      announce_delays: field('delays').checked,
      announce_cancellations: field('cancellations').checked,
    };
  } else {
    entry.announcements = null;
  }
}

/* ------------------------------------------------------------ the ribbon */

/** The week as the board will run it. First-match-wins is only hard to reason
 *  about until the gaps and the overlaps can be seen. */
const RIBBON_STEP = 10;

function renderRibbon() {
  const hours = Array.from({ length: 9 }, (_, i) => `<span>${String(i * 3).padStart(2, '0')}</span>`).join('');
  const rows = DAYS.map(([day, label]) => {
    const cells = [];
    for (let minute = 0; minute < 1440; minute += RIBBON_STEP) {
      const index = profileAt(day, minute);
      const last = cells[cells.length - 1];
      if (last && last.index === index) last.span += 1;
      else cells.push({ index, span: 1 });
    }
    const bar = cells.map((cell) => {
      const entry = entries()[cell.index];
      const title = entry ? `${profileLabel(entry, cell.index)} ${entry.start}–${entry.end}` : 'Base config';
      return `<span class="ribbon-cell" style="flex:${cell.span};background:${profileColour(cell.index)}"
                title="${escapeHtml(title)}"></span>`;
    }).join('');
    return `<div class="ribbon-row"><span class="ribbon-day">${label.slice(0, 3)}</span>
      <span class="ribbon-bar">${bar}</span></div>`;
  }).join('');
  profileRibbonEl.innerHTML = `<div class="ribbon-hours"><span class="ribbon-day"></span>
    <span class="ribbon-scale">${hours}</span></div>${rows}`;
}

/* ------------------------------------------------------------ the wiring */

function renderProfiles() {
  if (selectedProfile >= entries().length) selectedProfile = Math.max(0, entries().length - 1);
  renderProfileList();
  renderProfileEditor();
  renderRibbon();
  renderForceProfileOptions();
}

function readProfiles() {
  commitProfileEditor();
  return structuredClone(entries());
}

/** A blank profile takes today's board as its starting point: it is easier to
 *  change one station than to fill in six fields from nothing. */
function newProfile() {
  return {
    name: '',
    days: ['mon', 'tue', 'wed', 'thu', 'fri'],
    start: '06:30',
    end: '09:30',
    stations: structuredClone(config.stations),
    display: { theme: config.display.theme, clock: config.display.clock, show_calling_points: config.display.show_calling_points },
    announcements: null,
  };
}

profileListEl.addEventListener('click', (event) => {
  const pick = event.target.closest('[data-pick]');
  if (pick) {
    commitProfileEditor();
    selectedProfile = Number(pick.dataset.pick);
    renderProfiles();
    return;
  }
  const move = event.target.closest('[data-move]');
  if (move) {
    commitProfileEditor();
    const from = Number(move.dataset.move);
    const to = from + Number(move.dataset.by);
    const list = config.profiles.entries;
    [list[from], list[to]] = [list[to], list[from]];
    selectedProfile = to;
    renderProfiles();
    markDirty();
    return;
  }
  const drop = event.target.closest('[data-drop]');
  if (drop) {
    commitProfileEditor();
    config.profiles.entries.splice(Number(drop.dataset.drop), 1);
    renderProfiles();
    markDirty();
  }
});

profileEditorEl.addEventListener('change', (event) => {
  const body = event.target.dataset.profile;
  // Turning an override on gives it today's values to start from; turning it
  // off drops the key entirely, so the base config shows through again.
  if (body === 'stations-on' || body === 'display-on' || body === 'announce-on') {
    const entry = entries()[selectedProfile];
    if (body === 'stations-on') entry.stations = event.target.checked ? structuredClone(config.stations) : null;
    if (body === 'display-on') entry.display = event.target.checked ? { theme: config.display.theme } : null;
    if (body === 'announce-on') entry.announcements = event.target.checked ? { enabled: config.announcements.enabled } : null;
    renderProfileEditor();
    renderRibbon();
    markDirty();
    return;
  }
  commitProfileEditor();
  // The theme decides which options are on offer, so its own change redraws.
  if (body === 'theme') renderProfileEditor();
  renderProfileList();
  renderRibbon();
});

profileEditorEl.addEventListener('input', (event) => {
  if (event.target.type === 'time' || event.target.dataset.profile === 'name') {
    commitProfileEditor();
    renderProfileList();
    renderRibbon();
  }
});

profileEditorEl.addEventListener('click', (event) => {
  if (!event.target.closest('[data-add-station]')) return;
  const entry = entries()[selectedProfile];
  entry.stations = readStationList(profileEditorEl.querySelector('[data-profile-stations]'));
  if (entry.stations.length >= 2) return;
  const first = entry.stations[0];
  entry.stations.push({ crs: first.crs, mode: 'arrivals', name: null, rows: first.rows, announce: false });
  renderProfileStations(entry);
  markDirty();
});

document.getElementById('add-profile').addEventListener('click', () => {
  commitProfileEditor();
  config.profiles.entries.push(newProfile());
  selectedProfile = entries().length - 1;
  renderProfiles();
  markDirty();
  profileEditorEl.querySelector('[data-profile="name"]')?.focus();
});

function renderForceProfileOptions() {
  const select = document.getElementById('force-profile');
  const current = select.value;
  select.innerHTML = '<option value="">Auto (the clock)</option>' + entries()
    .map((entry, index) => {
      const name = escapeHtml(profileLabel(entry, index));
      return `<option value="${name}">${name}</option>`;
    })
    .join('');
  select.value = current;
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
  renderProfiles();
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
  draft.profiles.entries = readProfiles();
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
    const handover = status.profile_changes_at
      ? ` &rarr; ${status.next_profile || 'base config'} at ${formatTime(status.profile_changes_at)}`
      : '';
    statusList.innerHTML = `
      <dt>Profile</dt><dd>${status.active_profile ? ok(status.active_profile) : pill('', 'base config')}
        ${status.forced_profile ? bad(`forced: ${status.forced_profile}`) : ''}${handover}</dd>
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
    document.getElementById('force-profile').value = status.forced_profile || '';

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

/** The editor only holds the selected profile, so the others cannot be found
 *  by checkValidity: their fields are not in the document. */
function firstBadProfile() {
  commitProfileEditor();
  return entries().findIndex((entry) => !entry.name.trim() || !(entry.days || []).length);
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
  const badProfile = firstBadProfile();
  if (badProfile >= 0) {
    showTab('profiles');
    selectedProfile = badProfile;
    renderProfiles();
    showMessage('A profile needs a name and at least one day.', false);
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
  const profile = document.getElementById('force-profile').value;
  const post = (url, body) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const [sourceResponse, profileResponse] = await Promise.all([
    post('/api/source/force', { source }),
    post('/api/profile/force', { profile: profile || null }),
  ]);
  const applied = sourceResponse.ok && profileResponse.ok;
  // A profile can only be pinned once it is saved; the file is what the
  // backend knows about, not the draft in this page.
  const problem = profileResponse.ok ? 'Could not change the source.' : 'Save the profile first.';
  showMessage(applied ? `Source: ${source}. Profile: ${profile || 'auto'}.` : problem, applied);
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
