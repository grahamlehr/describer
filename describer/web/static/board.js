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
const SOURCE_LABELS = { rdm: 'Darwin', rtt: 'RTT' };
/** Pixels per second the calling-point marquee travels. */
const SCROLL_SPEED = 60;

let state = null;
let theme = null;
let themeName = null;
/** Handed to themes so one can repaint on its own schedule. */
const themeApi = { render: () => { render(); } };
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
  for (const list of boardsEl.querySelectorAll('.calling-points-list')) {
    list.textContent = '';
    list.classList.remove('scrolling');
  }

  try {
    const module = await import(`/static/themes/${name}.js`);
    theme = module;
    theme.attach?.(boardsEl, options, themeApi);
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
  // The height is shared between the configured slots, not the ones in use, so
  // the type does not resize every time a train drops off the board.
  rowsEl.style.setProperty('--rows', String(station.rows));
  const services = board.services.slice(0, station.rows);
  const seen = new Set();

  services.forEach((service, index) => {
    const row = rowFor(rowsEl, service);
    seen.add(service.id);
    row.dataset.status = service.status;
    row.dataset.index = String(index);
    row.classList.toggle('selected', index === 0);

    // In the tree before its cells are painted: a detached element has no
    // computed style, and splitflap sizes each field from the CSS --chars it
    // reads there. Painting first gave every cell a grid as wide as its own
    // text, so nothing was ever abbreviated or padded. append() on a row that
    // is already here re-orders it in place, so this stays the ordering step.
    rowsEl.append(row);

    const place = board.mode === 'arrivals' ? service.origin : service.destination;
    setText(row.querySelector('[data-field="time"]'), service.scheduled_time || '');
    setText(row.querySelector('[data-field="destination"]'), place || '');
    setText(row.querySelector('[data-field="platform"]'), service.platform || '-');
    setText(row.querySelector('[data-field="status"]'), statusText(service));
    setText(row.querySelector('[data-field="operator"]'), service.operator_code || '');
  });

  for (const child of Array.from(rowsEl.children)) {
    if (child.classList.contains('row') && !seen.has(child.dataset.id)) child.remove();
    // The placeholder outlives its welcome as soon as there is anything to show.
    if (child.classList.contains('empty') && services.length) child.remove();
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
  // A theme with less room may word this its own way; null means "you decide".
  const custom = theme?.statusText?.(service);
  if (custom != null) return custom;
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
  const rowsEl = boardEl.querySelector('.rows');
  const first = services[0];
  const points = show && first ? first.calling_points.map((p) => p.name) : [];

  // These stops belong to the top service, so they read directly under its row.
  const topRow = rowsEl.querySelector('.row');
  if (topRow && wrap.previousElementSibling !== topRow) {
    rowsEl.insertBefore(wrap, topRow.nextSibling);
  }

  // A hidden block claims no share of the height; the rows take it instead.
  // 0.9 of a row is a label and one line of stops with a little air; measured
  // rather than guessed, and it must match the default in base.css.
  rowsEl.style.setProperty('--calling-share', points.length ? '0.9' : '0');

  if (!points.length) {
    wrap.hidden = true;
    list.textContent = '';
    list.classList.remove('scrolling');
    return;
  }
  wrap.hidden = false;

  // A theme may page through them instead of scrolling; splitflap does.
  if (theme?.renderCallingPoints) {
    list.classList.remove('scrolling');
    list.style.removeProperty('--scroll-distance');
    theme.renderCallingPoints(list, points);
    return;
  }

  const text = points.join(' • ');
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

  // Which feed this board came from. Themes may style it or hide it.
  const source = boardEl.querySelector('.source');
  source.hidden = !board.source;
  source.dataset.source = board.source || '';
  source.textContent = SOURCE_LABELS[board.source] || board.source || '';

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
