/**
 * Board renderer.
 *
 * Receives whole-state snapshots over SSE and renders them into the shared
 * DOM. Themes never see the API: they get text through `renderText` and a
 * post-render hook, so adding a theme is a CSS file plus an optional module.
 */

const boardsEl = document.getElementById('boards');
const asleepEl = document.getElementById('asleep');
const connectionEl = document.getElementById('connection');
const themeLink = document.getElementById('theme-css');
const boardTemplate = document.getElementById('board-template');
const rowTemplate = document.getElementById('row-template');

const MODE_LABELS = { departures: 'Departures', arrivals: 'Arrivals' };
/** Pixels per second the calling-point marquee travels. */
const SCROLL_SPEED = 60;

let state = null;
let theme = null;
let themeName = null;
/** Clock offset so the board follows the Pi's clock, not the browser's. */
let clockOffsetMs = 0;

/* ---------------------------------------------------------------- theming */

async function applyTheme(name, options) {
  if (name === themeName) {
    theme?.configure?.(options);
    return;
  }
  theme?.detach?.();
  theme = null;
  themeName = name;
  document.body.dataset.theme = name;
  themeLink.href = `/static/themes/${name}.css`;

  // Cells rendered by the previous theme must go back to plain text.
  for (const cell of boardsEl.querySelectorAll('.cell')) {
    cell.textContent = cell.dataset.text || '';
    delete cell.dataset.rendered;
  }

  try {
    const module = await import(`/static/themes/${name}.js`);
    theme = module;
    theme.attach?.(boardsEl, options);
  } catch (err) {
    // A theme with no JS module is normal (modern); anything else is a bug.
    if (!String(err).includes('Failed to fetch dynamically imported module')) {
      console.error('Theme module failed', err);
    }
  }
}

function setText(el, text) {
  const value = text ?? '';
  if (el.dataset.text === value && el.dataset.rendered === '1') return;
  el.dataset.text = value;
  el.dataset.rendered = '1';
  if (theme?.renderText) theme.renderText(el, value);
  else el.textContent = value;
}

/* --------------------------------------------------------------- rendering */

function ensureBoards(count) {
  boardsEl.dataset.count = String(count);
  while (boardsEl.children.length > count) boardsEl.lastElementChild.remove();
  while (boardsEl.children.length < count) {
    boardsEl.append(boardTemplate.content.cloneNode(true));
  }
  return Array.from(boardsEl.children);
}

function rowFor(rowsEl, service) {
  let row = rowsEl.querySelector(`[data-id="${CSS.escape(service.id)}"]`);
  if (!row) {
    row = rowTemplate.content.firstElementChild.cloneNode(true);
    row.dataset.id = service.id;
  }
  return row;
}

function renderRows(boardEl, board, station) {
  const rowsEl = boardEl.querySelector('.rows');
  const services = board.services.slice(0, station.rows);
  const seen = new Set();

  services.forEach((service, index) => {
    const row = rowFor(rowsEl, service);
    seen.add(service.id);
    row.dataset.status = service.status;
    row.dataset.index = String(index);
    row.classList.toggle('selected', index === 0);

    const place = board.mode === 'arrivals' ? service.origin : service.destination;
    setText(row.querySelector('[data-field="time"]'), service.scheduled_time || '');
    setText(row.querySelector('[data-field="destination"]'), place || '');
    setText(row.querySelector('[data-field="platform"]'), service.platform || '-');
    setText(row.querySelector('[data-field="status"]'), statusText(service));
    setText(row.querySelector('[data-field="operator"]'), service.operator_code || '');
    rowsEl.append(row); // append re-orders an existing row in place
  });

  for (const row of Array.from(rowsEl.children)) {
    if (!seen.has(row.dataset.id)) row.remove();
  }

  if (!services.length) {
    const empty = rowsEl.querySelector('.empty') || document.createElement('div');
    empty.className = 'empty';
    empty.textContent = board.stale ? 'No data' : 'No services at this time';
    rowsEl.append(empty);
  }
  return services;
}

function statusText(service) {
  switch (service.status) {
    case 'cancelled': return 'Cancelled';
    case 'on_time': return 'On time';
    case 'delayed': return 'Delayed';
    case 'expected': return `Exp ${service.expected_time}`;
    default: return service.expected_time || '';
  }
}

function renderCallingPoints(boardEl, services, show) {
  const wrap = boardEl.querySelector('.calling-points');
  const list = wrap.querySelector('.calling-points-list');
  const first = services[0];
  const points = show && first ? first.calling_points.map((p) => p.name) : [];

  if (!points.length) {
    wrap.hidden = true;
    list.textContent = '';
    list.classList.remove('scrolling');
    return;
  }

  const text = points.join(' • ');
  wrap.hidden = false;
  if (list.textContent !== text) {
    list.textContent = text;
    list.classList.remove('scrolling');
    // Measure after layout, then start the marquee only if it overflows.
    requestAnimationFrame(() => {
      const overflow = list.scrollWidth - wrap.querySelector('.calling-points-track').clientWidth;
      if (overflow > 8) {
        list.style.setProperty('--scroll-distance', `${-overflow - 24}px`);
        list.style.setProperty('--scroll-duration', `${(overflow + 24) / SCROLL_SPEED + 4}s`);
        list.classList.add('scrolling');
      }
    });
  }
}

function renderBoard(boardEl, board, station, display) {
  boardEl.dataset.mode = board.mode;
  boardEl.querySelector('.station-name').textContent = board.name;
  boardEl.querySelector('.board-mode').textContent = MODE_LABELS[board.mode] || board.mode;
  boardEl.querySelector('.clock').hidden = !display.clock;

  const stale = boardEl.querySelector('.stale');
  stale.hidden = !board.stale;
  stale.textContent = board.stale ? `Data stale${board.error ? ` — ${board.error}` : ''}` : '';

  const services = renderRows(boardEl, board, station);
  renderCallingPoints(boardEl, services, display.show_calling_points);

  const messages = boardEl.querySelector('.messages');
  messages.hidden = board.messages.length === 0;
  messages.textContent = board.messages.join('  •  ');
}

async function render() {
  if (!state) return;
  const options = state.display.themes?.[state.display.theme] || {};
  await applyTheme(state.display.theme, options);

  asleepEl.hidden = state.display_on;
  boardsEl.hidden = !state.display_on;

  const boardEls = ensureBoards(state.boards.length);
  state.boards.forEach((board, index) => {
    renderBoard(boardEls[index], board, state.stations[index], state.display);
  });
  theme?.afterRender?.(boardsEl);
}

/* ------------------------------------------------------------------ clock */

function tickClock() {
  const now = new Date(Date.now() + clockOffsetMs);
  const text = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((n) => String(n).padStart(2, '0'))
    .join(':');
  for (const clock of boardsEl.querySelectorAll('.clock')) {
    if (clock.textContent !== text) clock.textContent = text;
  }
}

/* -------------------------------------------------------------------- SSE */

function connect() {
  const source = new EventSource('/api/stream');

  source.addEventListener('open', () => { connectionEl.hidden = true; });

  source.addEventListener('message', (event) => {
    connectionEl.hidden = true;
    try {
      state = JSON.parse(event.data);
    } catch (err) {
      console.error('Bad state frame', err);
      return;
    }
    clockOffsetMs = new Date(state.server_time).getTime() - Date.now();
    render();
  });

  source.addEventListener('error', () => {
    // EventSource reconnects on its own; just tell the viewer what is going on.
    connectionEl.hidden = false;
  });
}

connect();
setInterval(tickClock, 250);
tickClock();
