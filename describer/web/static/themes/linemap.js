/**
 * linemap theme: every train drawn as a line on a night map.
 *
 * Three things need JavaScript. Each service row gets a route under its text,
 * a line whose length is how long the journey takes and whose ticks are the
 * stops, which can only be sized once every row on the board is known (the
 * longest journey sets the scale), so rows are noted in `renderRow` and drawn
 * in `afterRender`. One station bar per board is added there too. And the top
 * service's block becomes a labelled strip map, paged on its own timer when
 * the stops will not fit.
 *
 * Everything this module adds to the page is tagged `lm-` and removed in
 * `detach`: rows and `.rows` outlive a theme switch.
 */

/** How long one page of a long strip map holds before the next. */
const PAGE_MS = 6000;
/** Eight line colours; the stylesheet owns the hues, this owns the choice. */
const LINE_COUNT = 8;
/** The route is never shorter than this share of the width, so a hop of two
 *  stops still reads as a line. */
const MIN_LENGTH = 0.25;
/** The stops on a map before the train, at most, so a train that has come a
 *  long way does not push the part that matters off the screen. */
const MAX_BEFORE = 4;
/** Spacing between stop names, in label font sizes. Rotated names need about
 *  two; with times printed under the line they need room for "15:24" too. */
const MIN_SPACING = 2.1;
const MIN_SPACING_TIMES = 3.1;
/** A few stops are not stretched across the screen: this many label font sizes
 *  between them is as far apart as they are drawn. */
const MAX_SPACING = 11;
/** The name cap in the stylesheet (.lm-label max-width), in label font sizes,
 *  and the 40 degrees it is hinged at. */
const LABEL_CAP = 11;
const COS40 = Math.cos((40 * Math.PI) / 180);
/** A continuation (dashed lead-in or tail) is this many spacings wide. */
const LEAD = 0.7;

/** Each row's service and board mode, noted by renderRow for afterRender. */
let pending = new WeakMap();
/** Strip map state per `.calling-points-list`: the data it holds, the page it
 *  is on, and the key of what is painted. A WeakMap, so a removed board is
 *  forgotten without being asked. */
let maps = new WeakMap();
/** The lists with a map, so the page timer can find them again; pruned of any
 *  that have left the tree. */
const mapLists = new Set();
let pageTimer = null;
let observer = null;
/** Counts render passes, so a map nobody asked for in the last one can go. */
let pass = 0;
let measureCtx = null;

export function attach() {
  pageTimer = setInterval(turnPages, PAGE_MS);
  // The stylesheet lands after the first render and the block's size is
  // settled by flex while the later rows take their share: measure again the
  // moment the block moves, not on the next page turn.
  observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const list = entry.target.querySelector('.calling-points-list');
      if (list) repaint(list);
    }
  });
}

export function configure() {}

export function detach() {
  clearInterval(pageTimer);
  pageTimer = null;
  observer?.disconnect();
  observer = null;
  mapLists.clear();
  // Fresh maps: board.js empties the lists on a switch, and a remembered key
  // would then claim the map was still painted.
  maps = new WeakMap();
  pending = new WeakMap();
  for (const el of document.querySelectorAll('.lm-route, .lm-station')) el.remove();
  for (const row of document.querySelectorAll('.row[data-line]')) delete row.dataset.line;
  for (const list of document.querySelectorAll('.calling-points-list')) {
    list.textContent = '';
    list.parentElement?.parentElement?.style.removeProperty('--lm-line');
  }
}

/* ------------------------------------------------------------------- cells */

/** board.js calls this for every cell instead of setting textContent. */
export function renderText(cell, text) {
  const field = cell.dataset.field;
  if (field === 'platform') {
    // "-" is board.js saying there is no platform yet; the pill is then empty
    // and the stylesheet hides it.
    cell.textContent = text === '-' ? '' : text;
    return;
  }
  if (field !== 'destination') {
    cell.textContent = text;
    return;
  }
  // "Abbey Wood via Whitechapel": the name is big, the via is quiet.
  const { name, via } = splitVia(text);
  cell.textContent = name;
  if (!via) return;
  const span = document.createElement('span');
  span.className = 'lm-via';
  span.textContent = via;
  cell.append(span);
}

function splitVia(text) {
  const match = /^(.*?)\s+(via\s+.*)$/i.exec(text || '');
  return match ? { name: match[1], via: match[2] } : { name: text || '', via: '' };
}

/** Plain wording is right for this board; the label says nothing. */
export function callingPointsLabel() {
  return '';
}

/* ------------------------------------------------------------------ routes */

/** A stable hash of the place name, so a destination keeps its colour. */
function lineFor(place) {
  const name = splitVia(place).name.trim().toLowerCase();
  let hash = 5381;
  for (let i = 0; i < name.length; i++) hash = ((hash * 33) ^ name.charCodeAt(i)) >>> 0;
  return hash % LINE_COUNT;
}

/**
 * board.js calls this for every row after its cells are painted. All it can
 * do alone is choose the colour and make sure there is somewhere to draw: how
 * long the line is depends on every other row, which afterRender sees.
 */
export function renderRow(row, service, mode) {
  pending.set(row, { service, mode });
  const place = mode === 'arrivals' ? service.origin : service.destination;
  const line = String(lineFor(place));
  if (row.dataset.line !== line) row.dataset.line = line;
  if (!row.querySelector(':scope > .lm-route')) {
    const route = document.createElement('div');
    route.className = 'lm-route';
    row.append(route);
  }
}

/** "HH:MM" as minutes since midnight, or null. */
function minutes(text) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(text || '');
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

/** Minutes from one time to a later one, over midnight if need be. */
function span(from, to) {
  if (from === null || to === null) return null;
  let diff = to - from;
  if (diff < 0) diff += 1440;
  return diff;
}

/**
 * What a row's route is made of, before any scale is known: the offsets in
 * minutes of each stop from this station, nearest first, the last being the
 * terminus. Offsets are null when a time is missing, which sends the route
 * to the fallback of full width and evenly spaced stops.
 */
function journeyOf(service, mode) {
  const arrivals = mode === 'arrivals';
  const here = minutes(service.scheduled_time);
  // Arrivals list their stops in running order, origin first, so the stop
  // nearest this station is the last one: flip them to nearest first.
  const stops = arrivals ? [...service.calling_points].reverse() : service.calling_points;
  const offsets = stops.map((p) => {
    const t = minutes(p.scheduled_time);
    return arrivals ? span(t, here) : span(here, t);
  });
  const known = offsets.length > 0 && offsets.every((o) => o !== null);
  return { count: stops.length, offsets: known ? offsets : null };
}

export function afterRender(boardsEl) {
  for (const rowsEl of boardsEl.querySelectorAll('.rows')) {
    if (!rowsEl.querySelector(':scope > .lm-station')) {
      const bar = document.createElement('div');
      bar.className = 'lm-station';
      rowsEl.append(bar);
    }
    // A bar with no lines leaving it says nothing: leave it out until a train
    // gives it something to join.
    rowsEl.querySelector(':scope > .lm-station').hidden = !rowsEl.querySelector(':scope > .row');
    drawRoutes(rowsEl);
  }
  // board.js does not say when the top train has no stops: it empties the
  // list and hides the block, and on a split screen then shows the block
  // again, blank, to keep the two halves level. A map remembered from the
  // train before would be repainted into that blank by the observer, so drop
  // any map this pass did not ask for.
  for (const list of mapLists) {
    if (list.isConnected && maps.get(list)?.pass === pass) continue;
    mapLists.delete(list);
    maps.delete(list);
    if (list.isConnected) list.textContent = '';
  }
  pass += 1;
}

/** Size every route on one board against its longest journey. */
function drawRoutes(rowsEl) {
  const rows = [];
  for (const row of rowsEl.querySelectorAll(':scope > .row')) {
    const note = pending.get(row);
    if (note) rows.push({ row, ...note, journey: journeyOf(note.service, note.mode) });
  }
  let longest = 0;
  for (const { service, journey } of rows) {
    if (service.status === 'cancelled' || !journey.offsets) continue;
    longest = Math.max(longest, journey.offsets[journey.offsets.length - 1]);
  }
  for (const { row, service, journey } of rows) {
    const route = row.querySelector(':scope > .lm-route');
    if (route) paintRoute(route, service, journey, longest);
  }
}

/** Where the line ends and where its ticks fall, as fractions of the width. */
function routeShape(service, journey, longest) {
  const { count, offsets } = journey;
  let length = 1;
  const ticks = [];
  if (count && offsets && longest > 0) {
    const last = offsets[count - 1];
    // Square root: a long journey would otherwise flatten every short one to
    // nothing, and the scale is the same for the line and for its ticks.
    length = Math.min(1, Math.max(MIN_LENGTH, Math.sqrt(last / longest)));
    const scale = last > 0 ? length / Math.sqrt(last / longest) : 0;
    for (let i = 0; i < count - 1; i++) {
      ticks.push(last > 0 ? Math.sqrt(offsets[i] / longest) * scale : (length * (i + 1)) / count);
    }
  } else {
    for (let i = 0; i < count - 1; i++) ticks.push((i + 1) / count);
  }
  if (service.status === 'cancelled') ticks.length = 0;
  return { length, ticks };
}

function paintRoute(route, service, journey, longest) {
  const { length, ticks } = routeShape(service, journey, longest);
  const key = [length.toFixed(3), ticks.map((t) => t.toFixed(3)).join(','), service.status === 'cancelled'].join('|');
  if (route.dataset.key === key) return;
  route.dataset.key = key;
  const track = document.createElement('div');
  track.className = 'lm-track';
  const line = document.createElement('div');
  line.className = 'lm-line';
  line.style.width = `${(length * 100).toFixed(2)}%`;
  track.append(line);
  for (const t of ticks) {
    const tick = document.createElement('i');
    tick.className = 'lm-tick';
    tick.style.left = `${(t * 100).toFixed(2)}%`;
    track.append(tick);
  }
  const end = document.createElement('i');
  end.className = 'lm-end';
  end.style.left = `${(length * 100).toFixed(2)}%`;
  track.append(end);
  route.replaceChildren(track);
}

/* ------------------------------------------------------------- the strip map */

/** The same rules as board.js's stopTime, so the map and the stops agree. */
function stopTime(point) {
  if (point.cancelled) return null;
  if (point.actual_time) return { text: point.actual_time, late: false };
  if (point.expected_time && /^\d{2}:\d{2}$/.test(point.expected_time)) {
    return { text: point.expected_time, late: point.scheduled_time != null && point.expected_time > point.scheduled_time };
  }
  return point.scheduled_time ? { text: point.scheduled_time, late: false } : null;
}

/**
 * The nodes of the map, in running order. The whole journey when the feed
 * has it (marked `here`), trimmed to a few stops before this station; else
 * this station and the stops the board does have, in the order they run.
 */
function buildNodes(list, rawPoints, service) {
  const board = list.closest('.board');
  const arrivals = board?.dataset.mode === 'arrivals';
  const journey = service.journey || [];
  const hereAt = journey.findIndex((p) => p.here);
  if (journey.length && hereAt >= 0) {
    const start = Math.max(0, hereAt - MAX_BEFORE);
    return { nodes: journey.slice(start).map(toNode), cut: start > 0 };
  }
  const name = board?.querySelector('.station-name')?.textContent || '';
  const here = {
    name,
    scheduled_time: service.scheduled_time,
    expected_time: service.expected_time,
    here: true,
  };
  const points = rawPoints.map(toNode);
  return { nodes: arrivals ? [...points, toNode(here)] : [toNode(here), ...points], cut: false };
}

function toNode(point) {
  return {
    name: point.name,
    here: Boolean(point.here),
    passed: Boolean(point.actual_time),
    cancelled: Boolean(point.cancelled),
    time: stopTime(point),
  };
}

/**
 * board.js hands us the block's list. It has cleared nothing: we own what is
 * inside, and are called again every few seconds with the same data, so a
 * pass that changes nothing returns before touching the DOM.
 */
export function renderCallingPoints(list, points, rawPoints, service, showTimes) {
  const { nodes, cut } = buildNodes(list, rawPoints, service);
  const place = list.closest('.board')?.dataset.mode === 'arrivals' ? service.origin : service.destination;
  const data = {
    nodes,
    cut,
    showTimes: Boolean(showTimes),
    line: lineFor(place),
    position: service.position || null,
  };
  const dataKey = JSON.stringify(data);
  let state = maps.get(list);
  if (!state || state.dataKey !== dataKey) {
    state = { data, dataKey, page: state?.dataKey === dataKey ? state.page : 0, painted: '' };
    maps.set(list, state);
  }
  state.pass = pass;
  mapLists.add(list);
  const wrap = list.closest('.calling-points');
  if (wrap) observer?.observe(wrap);
  repaint(list);
}

function repaint(list) {
  const state = maps.get(list);
  const wrap = list.closest('.calling-points');
  if (!state || !wrap || wrap.hidden) return;
  const width = wrap.clientWidth;
  const height = wrap.clientHeight;
  if (!width || !height) return;
  const style = getComputedStyle(wrap);
  const font = parseFloat(style.fontSize);
  const layout = plan(state.data, width, font, style);
  state.pages = layout.pages.length;
  state.page = Math.min(state.page, layout.pages.length - 1);
  const key = [state.dataKey, width, height, font, state.page].join('|');
  if (state.painted === key) return;
  state.painted = key;
  wrap.style.setProperty('--lm-line', `var(--line-${state.data.line})`);
  draw(list, state.data, layout, state.page, width, height, font);
}

function turnPages() {
  for (const list of mapLists) {
    const state = maps.get(list);
    if (!list.isConnected || !state || !(state.pages > 1)) continue;
    state.page = (state.page + 1) % state.pages;
    repaint(list);
  }
}

/** How far a rotated name reaches to the right of its dot, in px. */
function reach(name, bold, font, style) {
  measureCtx ||= document.createElement('canvas').getContext('2d');
  measureCtx.font = `${bold ? 700 : 400} ${font}px ${style.fontFamily}`;
  const wide = Math.min(measureCtx.measureText(name).width + font * 0.2, LABEL_CAP * font);
  return wide * COS40 + font * 0.5;
}

/**
 * Work out the pages: how many stops fit at the least spacing a rotated name
 * needs, then the nodes on each page, balanced so no page is left with one.
 */
function plan(data, width, font, style) {
  const { nodes, showTimes } = data;
  const lead = font * 1.4;
  const minSpacing = font * (showTimes ? MIN_SPACING_TIMES : MIN_SPACING);
  const lastNode = nodes[nodes.length - 1];
  const tail = reach(lastNode.name, true, font, style);
  const capacity = Math.max(2, Math.floor((width - lead - tail) / minSpacing));
  const count = Math.ceil(nodes.length / capacity);
  const size = Math.ceil(nodes.length / count);
  const pages = [];
  for (let i = 0; i < nodes.length; i += size) pages.push([i, Math.min(nodes.length, i + size)]);
  return { pages, lead, capacity };
}

function el(tag, className, parent) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  parent?.append(node);
  return node;
}

function draw(list, data, layout, pageIndex, width, height, font) {
  const { nodes, showTimes, position, cut } = data;
  const [from, to] = layout.pages[pageIndex];
  const pageNodes = nodes.slice(from, to);
  const hasLead = from > 0 || cut;
  const hasMore = to < nodes.length;
  const style = getComputedStyle(list);
  // Room for the last name on this page, which stands up and to the right.
  const lastOn = pageNodes[pageNodes.length - 1];
  // The chevron and dashed tail run along the line, under the name, so a page
  // that continues needs the name's room too, and a little line beyond it.
  const reachOn = reach(lastOn.name, lastOn.here || from + pageNodes.length === nodes.length, font, style);
  const tail = hasMore ? Math.max(reachOn, font * 3) : reachOn;
  const left = layout.lead;
  const gaps = Math.max(1, pageNodes.length - 1) + (hasLead ? LEAD : 0);
  const spacing = Math.min(font * MAX_SPACING, (width - left - tail) / gaps);
  const lineY = height - font * (showTimes ? 2.1 : 1.1);
  const x0 = left + (hasLead ? LEAD * spacing : 0);
  const xOf = (index) => x0 + (index - from) * spacing;

  // Where the train is, as a fractional index into all the nodes.
  let trainAt = null;
  let waiting = false;
  if (position) {
    if (position.state === 'not_started') {
      trainAt = 0;
      waiting = true;
    } else if (position.last) {
      const last = nodes.findIndex((n) => n.name === position.last);
      if (last >= 0) trainAt = last + (last < nodes.length - 1 ? 0.5 : 0);
      else if (cut) trainAt = -LEAD / 2;
    }
  }
  // The train is ahead of this page's first node: it rides the lead-in.
  const trainX = trainAt === null ? null
    : trainAt < from && hasLead ? Math.max(font, (x0 - LEAD * spacing * 0.5))
    : trainAt >= from && trainAt <= to - 1 ? xOf(trainAt)
    : null;

  // A stop already left (an actual time) dims the line behind it too.
  let passedTo = null;
  pageNodes.forEach((n, i) => { if (n.passed) passedTo = x0 + i * spacing; });
  const dimTo = trainX ?? passedTo;

  const frag = document.createDocumentFragment();
  const endX = hasMore ? width : xOf(to - 1);
  const seg = (x, w, flag) => {
    const s = el('div', 'lm-seg', frag);
    s.style.left = `${x}px`;
    s.style.top = `${lineY}px`;
    s.style.width = `${Math.max(0, w)}px`;
    if (flag) s.dataset[flag] = '';
  };
  if (hasLead) seg(0, x0, 'dash');
  const solidFrom = hasLead ? x0 : 0;
  const dimEnd = dimTo === null ? solidFrom : Math.min(Math.max(dimTo, solidFrom), hasMore ? xOf(to - 1) : endX);
  if (dimEnd > solidFrom) seg(solidFrom, dimEnd - solidFrom, 'dim');
  const litEnd = hasMore ? xOf(to - 1) : endX;
  seg(dimEnd, litEnd - dimEnd);
  if (hasMore) {
    seg(litEnd, width - litEnd - font * 1.1, 'dash');
    const chev = el('i', 'lm-chev', frag);
    chev.style.left = `${width - font * 0.9}px`;
    chev.style.top = `${lineY}px`;
    const page = el('span', 'lm-page', frag);
    page.style.top = `${lineY + font * 0.9}px`;
    page.textContent = `${pageIndex + 1} / ${layout.pages.length}`;
  }

  pageNodes.forEach((n, i) => {
    const index = from + i;
    const node = el('div', 'lm-node', frag);
    node.style.left = `${x0 + i * spacing}px`;
    node.style.top = `${lineY}px`;
    node.dataset.kind = n.here ? 'here' : index === nodes.length - 1 ? 'end' : 'stop';
    if (n.passed) node.dataset.passed = '';
    if (n.cancelled) node.dataset.cancelled = '';
    el('i', 'lm-dot', node);
    const label = el('span', 'lm-label', node);
    label.textContent = n.name;
    if (showTimes && n.time) {
      const time = el('span', 'lm-time', node);
      time.textContent = n.time.text;
      if (n.time.late) time.dataset.late = '';
    }
  });

  if (trainX !== null) {
    const train = el('i', 'lm-train', frag);
    train.style.left = `${trainX}px`;
    train.style.top = `${lineY}px`;
    if (waiting) train.dataset.waiting = '';
  }
  list.replaceChildren(frag);
}
