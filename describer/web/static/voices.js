/**
 * Recorded voices, in /admin → Announcements. The board writes a script, the
 * owner records it with a voice service and uploads the audio, the board cuts
 * it into clips, and a reviewed, built voice is switched on here. All the work
 * is on the board: this module only talks to /api/voices (design note 23).
 *
 * Nothing here has a `name` attribute: the config form saves every named
 * field, and these are not config. Events stop at the container for the same
 * reason (they would otherwise mark the form unsaved).
 */

const AUDIO_TYPES = '.mp3,.wav,.m4a,.flac,.ogg';
const POLL_MS = 2000;

const root = document.getElementById('voices');
const panel = root.closest('[data-panel]');
const listEl = document.getElementById('voices-list');
const detailEl = document.getElementById('voices-detail');
const noteEl = document.getElementById('voices-note');

const state = {
  list: null, // the last GET /api/voices
  openSlug: '',
  detail: null, // the last VoiceDetail for openSlug
  uploads: {}, // `${slug}/${number}` -> percent
  pending: new Set(), // actions in flight, so a double click does nothing
  rendered: '', // last detail markup, so a poll that changed nothing redraws nothing
  timer: null,
  onChange: () => {},
};

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 MB';
  if (bytes < 1e6) return `${Math.max(1, Math.round(bytes / 1e3))} kB`;
  if (bytes < 1e9) return `${(bytes / 1e6).toFixed(bytes < 1e8 ? 1 : 0)} MB`;
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

/* ------------------------------------------------------------ messages */

/** A message in the page's notice style; an error stays until the next action. */
function note(text, kind = 'bad') {
  noteEl.textContent = text || '';
  noteEl.className = `hint voice-note ${kind}`;
  noteEl.hidden = !text;
  if (text && kind === 'ok') {
    setTimeout(() => { if (noteEl.textContent === text) note(''); }, 5000);
  }
}

function describeError(detail, fallback) {
  if (Array.isArray(detail)) {
    return detail.map((e) => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`.replace(/^: /, '')).join('\n');
  }
  return detail ? String(detail) : fallback;
}

/* ------------------------------------------------------------- requests */

/** Resolves to { ok, status, data }; a network failure becomes a readable error, not a throw. */
async function request(method, url, body) {
  try {
    const options = { method };
    if (body !== undefined) {
      options.headers = { 'Content-Type': 'application/json' };
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, data };
  } catch (error) {
    return { ok: false, status: 0, data: { detail: `Could not reach the board: ${error}` } };
  }
}

/** Send a File as the raw body, reporting progress; the same result shape as request(). */
function sendFile(method, url, file, contentType, onProgress) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.setRequestHeader('Content-Type', contentType || 'application/octet-stream');
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* no body */ }
      resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, data });
    };
    xhr.onerror = () => resolve({ ok: false, status: 0, data: { detail: 'The upload did not reach the board. Check the connection and try again.' } });
    xhr.onabort = xhr.onerror;
    xhr.send(file);
  });
}

function fail(result, fallback) {
  note(describeError(result.data && result.data.detail, `${fallback} (${result.status || 'no answer'}).`));
}

const apiPath = (slug, rest = '') => `/api/voices/${encodeURIComponent(slug)}${rest}`;

/* --------------------------------------------------------------- state */

function setList(data) {
  state.list = data;
  renderList();
  renderDetail();
}

function setDetail(detail) {
  if (!detail || detail.slug !== state.openSlug) return;
  state.detail = detail;
  // The detail is a superset of the summary: keep the list row in step with it.
  if (state.list) {
    const at = state.list.voices.findIndex((v) => v.slug === detail.slug);
    if (at >= 0) state.list.voices[at] = { ...state.list.voices[at], ...detail };
    renderList();
  }
  renderDetail();
  schedulePoll();
}

async function loadList() {
  const result = await request('GET', '/api/voices');
  if (!result.ok) {
    fail(result, 'Could not load the voices');
    return;
  }
  setList(result.data);
  if (state.openSlug && !result.data.voices.some((v) => v.slug === state.openSlug)) closeVoice();
}

async function loadDetail() {
  if (!state.openSlug) return;
  const slug = state.openSlug;
  const result = await request('GET', apiPath(slug));
  if (slug !== state.openSlug) return;
  if (!result.ok) {
    if (result.status === 404) closeVoice();
    else fail(result, 'Could not load that voice');
    return;
  }
  setDetail(result.data);
}

function panelVisible() {
  return !panel.hidden;
}

function isBusy(detail) {
  return Boolean(detail && (detail.building || detail.parts.some((p) => p.state === 'queued' || p.state === 'cutting')));
}

function stopPolling() {
  clearTimeout(state.timer);
  state.timer = null;
}

function schedulePoll() {
  stopPolling();
  if (!state.openSlug || !panelVisible() || !isBusy(state.detail)) return;
  state.timer = setTimeout(async () => {
    state.timer = null;
    if (!panelVisible() || !state.openSlug) return;
    const wasBusy = isBusy(state.detail);
    await loadDetail();
    // Free space and the other rows change as work finishes.
    if (wasBusy && !isBusy(state.detail)) {
      state.onChange();
      await loadList();
    }
    schedulePoll();
  }, POLL_MS);
}

function openVoice(slug) {
  state.openSlug = slug;
  state.detail = null;
  state.rendered = '';
  renderList();
  renderDetail();
  loadDetail();
}

function closeVoice() {
  stopPolling();
  state.openSlug = '';
  state.detail = null;
  state.rendered = '';
  renderList();
  renderDetail();
}

/* ------------------------------------------------------------ the list */

function voiceStatus(voice) {
  if (voice.busy) return 'Working…';
  const bits = [];
  if (voice.imported) {
    bits.push(`Ready-made pack, ${plural(voice.clips, 'clip', 'clips')}`);
  } else {
    if (voice.parts_total) bits.push(`${voice.parts_recorded} of ${plural(voice.parts_total, 'part', 'parts')} recorded`);
    if (voice.to_check) bits.push(`${plural(voice.to_check, 'clip', 'clips')} to check`);
    if (voice.built) {
      const when = state.detail && state.detail.slug === voice.slug && state.detail.built_at
        ? `Built ${state.detail.built_at.slice(0, 10)}` : 'Built';
      bits.push(voice.stale ? `${when} · changed since` : 'Ready');
    } else if (voice.parts_total && voice.parts_recorded === voice.parts_total) {
      bits.push('Not built yet');
    }
  }
  return bits.join(' · ');
}

function renderList() {
  const data = state.list;
  if (!data) {
    listEl.innerHTML = '<li class="hint">Loading…</li>';
    return;
  }
  const rows = [];
  const piperInUse = !data.in_use;
  rows.push(`<li class="voice-row">
    <div class="voice-title"><strong>Piper (built-in voice)</strong>
      <small>The standard voice, always available</small></div>
    ${piperInUse ? '<span class="pill ok">In use</span>' : ''}
    <div class="voice-buttons">${piperInUse ? '' : '<button type="button" class="ghost" data-act="use" data-slug="">Use</button>'}</div>
  </li>`);
  for (const voice of data.voices) {
    const slug = escapeHtml(voice.slug);
    const open = voice.slug === state.openSlug;
    const useButton = voice.in_use
      ? `<button type="button" class="ghost" data-act="use" data-slug="">Stop using</button>`
      : `<button type="button" class="ghost" data-act="use" data-slug="${slug}"${voice.built ? '' : ' disabled title="Build this voice first"'}>Use</button>`;
    rows.push(`<li class="voice-row"${open ? ' data-open' : ''}>
      <div class="voice-title"><strong>${escapeHtml(voice.name)}</strong>
        <small>${escapeHtml(voiceStatus(voice))} · ${escapeHtml(formatBytes(voice.pack_bytes + voice.working_bytes))} on disk</small></div>
      ${voice.in_use ? '<span class="pill ok">In use</span>' : ''}
      <div class="voice-buttons">
        ${useButton}
        <button type="button" class="ghost" data-act="${open ? 'close' : 'open'}" data-slug="${slug}">${open ? 'Close' : 'Open'}</button>
        <button type="button" class="danger" data-act="delete" data-slug="${slug}">Delete</button>
      </div>
    </li>`);
  }
  listEl.innerHTML = rows.join('');

  document.getElementById('voices-ffmpeg').hidden = data.ffmpeg !== false;
  document.getElementById('voices-off').hidden = !(data.in_use && data.enabled === false);
  document.getElementById('voices-space').textContent =
    `Free space on the board: ${formatBytes(data.free_bytes)}.`;
}

/* ----------------------------------------------------------- the detail */

function partState(part, slug) {
  const uploading = state.uploads[`${slug}/${part.number}`];
  if (uploading !== undefined) {
    return `<span class="voice-state">Uploading… <progress max="100" value="${uploading}"></progress> ${uploading}%</span>`;
  }
  switch (part.state) {
    case 'queued': return '<span class="voice-state">Queued</span>';
    case 'cutting': return '<span class="voice-state">Cutting…</span>';
    case 'cut': {
      const extra = part.to_check ? `, ${part.to_check} to check` : '';
      return `<span class="voice-state ok">${escapeHtml(plural(part.clips, 'clip', 'clips'))}${extra}</span>`;
    }
    case 'failed':
      return `<span class="voice-state bad">Failed: ${escapeHtml(part.error || 'unknown error')}
        <button type="button" class="ghost" data-act="cut" data-part="${escapeHtml(part.number)}">Try again</button></span>`;
    default: return '<span class="voice-state">Waiting for a recording</span>';
  }
}

function partRow(part, detail) {
  const number = escapeHtml(part.number);
  const slug = detail.slug;
  const uploading = state.uploads[`${slug}/${part.number}`] !== undefined;
  const working = part.state === 'queued' || part.state === 'cutting';
  const audio = part.audio
    ? `<small>${escapeHtml(part.audio.name)}, ${escapeHtml(formatBytes(part.audio.bytes))}</small>` : '';
  return `<li class="voice-part">
    <div><strong>${number}</strong> ${escapeHtml(partLabel(part.label))}
      <small>· ${escapeHtml(plural(part.items, 'item', 'items'))}, ${escapeHtml(part.chars.toLocaleString('en-GB'))} characters</small>
      ${audio}</div>
    <div class="voice-part-actions">
      <a class="voice-link" href="${apiPath(slug, `/script/${encodeURIComponent(part.number)}`)}" download>Download</a>
      <label class="voice-file">${part.audio ? 'Replace recording' : 'Upload recording'}
        <input type="file" class="visually-hidden" accept="${AUDIO_TYPES}" data-part="${number}"${uploading || working ? ' disabled' : ''}>
      </label>
      ${part.audio && !uploading && !working
        ? `<button type="button" class="danger" data-act="remove-audio" data-part="${number}">Remove recording</button>` : ''}
    </div>
    ${partState(part, slug)}
  </li>`;
}

function renderDetail() {
  const detail = state.detail;
  if (!state.openSlug || !detail || detail.slug !== state.openSlug) {
    detailEl.hidden = !state.openSlug;
    detailEl.innerHTML = state.openSlug ? '<p class="hint">Loading…</p>' : '';
    state.rendered = '';
    return;
  }
  const working = detail.parts.some((p) => p.state === 'queued' || p.state === 'cutting');
  const anyCut = detail.parts.some((p) => p.state === 'cut');
  const uploading = Object.keys(state.uploads).some((k) => k.startsWith(`${detail.slug}/`));
  const builtNow = detail.built && !detail.stale && !detail.building;

  let buildReason = '';
  if (detail.building) buildReason = '';
  else if (working) buildReason = 'Wait for the cutting to finish.';
  else if (uploading) buildReason = 'Wait for the upload to finish.';
  else if (!anyCut) buildReason = 'Upload and cut at least one recording first.';
  const buildLabel = detail.building ? 'Building…' : (builtNow ? 'Built' : 'Build voice');
  const buildDisabled = detail.building || builtNow || Boolean(buildReason);

  const checkLabel = detail.to_check > 0 ? `Check ${plural(detail.to_check, 'clip', 'clips')}` : 'Review all clips';
  const reviewHref = `/admin/voice-review?voice=${encodeURIComponent(detail.slug)}`;

  let useHint = '';
  if (!detail.built) useHint = 'Build the voice first.';
  else if (detail.stale) useHint = 'Changed since it was built. Build it again to include your latest changes, or use it as it is.';

  const html = `
    <h3>${escapeHtml(detail.name)}</h3>
    ${detail.imported ? '<p class="hint">This is a ready-made pack, so there is nothing to record.</p>' : `
    <ol>
      <li>Download a part below. It is the text to record.</li>
      <li>Paste it into your voice service and generate the audio.</li>
      <li>Upload the audio file here against the same part.</li>
    </ol>
    <p class="hint">Do part 00 first and listen to the result before recording the rest. It is short and shows whether the voice and pacing are right.</p>
    <div class="inline-actions">
      <a class="voice-link" href="${apiPath(detail.slug, '/script.zip')}" download>Download all parts (.zip)</a>
    </div>
    <ul class="voice-parts">${detail.parts.map((p) => partRow(p, detail)).join('')}</ul>`}
    ${detail.build_error ? `<p class="hint voice-note bad">The last build failed: ${escapeHtml(detail.build_error)}</p>` : ''}
    <div class="inline-actions">
      <a class="voice-link" href="${reviewHref}" target="_blank" rel="noopener">${escapeHtml(checkLabel)}</a>
      ${detail.rejected > 0 ? `<button type="button" class="ghost" data-act="extra-redo">Re-record rejected clips (${detail.rejected})</button>` : ''}
      ${detail.imported ? '' : `<button type="button" data-act="build"${buildDisabled ? ' disabled' : ''}>${buildLabel}</button>`}
      <button type="button" data-act="use" data-slug="${escapeHtml(detail.slug)}"${detail.built && !detail.in_use ? '' : ' disabled'}>${detail.in_use ? 'In use' : 'Use this voice'}</button>
      ${detail.working_bytes > 0 && !detail.imported
        ? '<button type="button" class="ghost" data-act="free">Free up space</button>' : ''}
    </div>
    ${buildReason && !detail.imported ? `<p class="hint">${escapeHtml(buildReason)}</p>` : ''}
    ${useHint ? `<p class="hint">${escapeHtml(useHint)}</p>` : ''}`;

  // A poll that changed nothing must not redraw: it would close an open file chooser.
  if (html === state.rendered) return;
  state.rendered = html;
  detailEl.hidden = false;
  detailEl.innerHTML = html;
}

/* -------------------------------------------------------------- actions */

async function guarded(key, fn) {
  if (state.pending.has(key)) return;
  state.pending.add(key);
  try { await fn(); } finally { state.pending.delete(key); }
}

async function useVoice(slug) {
  await guarded('use', async () => {
    note('');
    const result = await request('POST', '/api/voices/use', { slug });
    if (!result.ok) { fail(result, 'Could not change the voice'); return; }
    setList(result.data);
    note(slug ? 'That voice will speak the next announcement.' : 'Piper will speak the next announcement.', 'ok');
    state.onChange();
  });
}

async function deleteVoice(slug) {
  const voice = state.list && state.list.voices.find((v) => v.slug === slug);
  const name = voice ? voice.name : slug;
  if (!confirm(`Delete the voice "${name}", its recordings and its clips? This cannot be undone.`)) return;
  await guarded(`delete:${slug}`, async () => {
    note('');
    const result = await request('DELETE', apiPath(slug));
    if (!result.ok) { fail(result, 'Could not delete that voice'); return; }
    if (slug === state.openSlug) closeVoice();
    setList(result.data);
    state.onChange();
  });
}

async function uploadAudio(number, file) {
  const slug = state.openSlug;
  const key = `${slug}/${number}`;
  if (state.uploads[key] !== undefined) return;
  note('');
  state.uploads[key] = 0;
  renderDetail();
  const url = `${apiPath(slug, `/audio/${encodeURIComponent(number)}`)}?name=${encodeURIComponent(file.name)}`;
  const result = await sendFile('PUT', url, file, file.type, (percent) => {
    state.uploads[key] = percent;
    renderDetail();
  });
  delete state.uploads[key];
  if (!result.ok) {
    fail(result, `Could not upload that recording for part ${number}`);
    renderDetail();
    return;
  }
  setDetail(result.data);
}

async function partAction(method, rest, failure) {
  const slug = state.openSlug;
  note('');
  const result = await request(method, apiPath(slug, rest));
  if (!result.ok) { fail(result, failure); return; }
  setDetail(result.data);
}

async function addExtra(kind, button) {
  const slug = kind === 'topup' ? state.list && state.list.in_use : state.openSlug;
  if (!slug) {
    note('Choose the recorded voice you are using first, then try again.');
    return;
  }
  await guarded(`extra:${kind}`, async () => {
    note('');
    if (button) button.disabled = true;
    const result = await request('POST', apiPath(slug, '/extra'), { kind });
    if (button) button.disabled = false;
    if (!result.ok) { fail(result, 'Could not add to the script'); return; }
    note(kind === 'topup'
      ? 'Added a new part to the voice\'s script. Open the voice to download it and record it.'
      : 'Added the rejected clips as a new part. Download it from the list of parts and record it.', 'ok');
    if (slug !== state.openSlug) openVoice(slug);
    else setDetail(result.data);
    loadList();
  });
}

async function createVoice(button) {
  const name = document.getElementById('voices-name').value.trim();
  const near = document.getElementById('voices-near').value
    .split(/[\s,]+/).map((code) => code.trim().toUpperCase()).filter(Boolean);
  const km = Number(document.getElementById('voices-km').value);
  const all = document.getElementById('voices-all').checked;
  const board = document.getElementById('voices-board').checked;
  if (!name) { note('Give the voice a name.'); return; }
  if (near.some((code) => !/^[A-Z]{3}$/.test(code))) {
    note('Stations are three-letter codes, such as PAD or RDG, separated by commas.');
    return;
  }
  if (!all && !near.length && !board) {
    note('Choose at least one station, or tick one of the other station options.');
    return;
  }
  if (!Number.isFinite(km) || km < 0) { note('Distance must be a number of kilometres.'); return; }
  const limit = document.getElementById('voices-limit-100000').checked ? 100000 : 5000;
  await guarded('create', async () => {
    note('');
    button.disabled = true;
    const label = button.textContent;
    button.textContent = 'Writing the script…';
    const result = await request('POST', '/api/voices', {
      name, near, km, board_stations: board, all_stations: all,
      one_reading: document.getElementById('voices-once').checked, paste_limit: limit,
    });
    button.disabled = false;
    button.textContent = label;
    if (!result.ok) {
      note(result.status === 409
        ? describeError(result.data.detail, 'There is already a voice with that name.')
        : describeError(result.data.detail, `Could not create the voice (${result.status || 'no answer'}).`));
      return;
    }
    document.getElementById('voices-name').value = '';
    state.openSlug = result.data.slug;
    state.detail = result.data;
    await loadList();
    setDetail(result.data);
    detailEl.scrollIntoView({ block: 'nearest' });
  });
}

async function importPack(file) {
  const progress = document.getElementById('voices-import-progress');
  note('');
  progress.hidden = false;
  progress.value = 0;
  const result = await sendFile('PUT', '/api/voices/import', file, 'application/zip', (p) => { progress.value = p; });
  progress.hidden = true;
  if (!result.ok) { fail(result, 'Could not install that voice pack'); return; }
  note('Voice pack installed. Choose Use to switch to it.', 'ok');
  await loadList();
  openVoice(result.data.slug);
}

/* -------------------------------------------------------------- wiring */

// None of this is config: keep it from marking the main form unsaved, and
// keep Enter in a text box from submitting that form.
for (const type of ['input', 'change']) {
  root.addEventListener(type, (event) => event.stopPropagation());
}
root.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && event.target.matches('input')) {
    event.preventDefault();
    if (event.target.closest('#voices-new')) document.getElementById('voices-create').click();
  }
});

root.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-act]');
  if (!button || button.disabled) return;
  const { act, slug, part } = button.dataset;
  if (act === 'use') useVoice(slug || '');
  else if (act === 'open') openVoice(slug);
  else if (act === 'close') closeVoice();
  else if (act === 'delete') deleteVoice(slug);
  else if (act === 'cut') guarded(`cut:${part}`, () => partAction('POST', `/cut/${encodeURIComponent(part)}`, 'Could not start cutting'));
  else if (act === 'remove-audio') {
    if (!confirm(`Remove the recording for part ${part}? Its clips go too.`)) return;
    guarded(`rm:${part}`, () => partAction('DELETE', `/audio/${encodeURIComponent(part)}`, 'Could not remove that recording'));
  } else if (act === 'extra-redo') addExtra('redo', button);
  else if (act === 'build') guarded('build', () => partAction('POST', '/build', 'Could not start the build'));
  else if (act === 'free') {
    if (!confirm('Free up space? This removes the recordings and loose clips and keeps the built voice. To adjust cuts later you will need to upload the recordings again.')) return;
    guarded('free', () => partAction('DELETE', '/working', 'Could not free up space').then(() => loadList()));
  }
});

root.addEventListener('change', (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) return;
  if (input.type === 'file' && input.dataset.part) {
    const file = input.files[0];
    if (file) uploadAudio(input.dataset.part, file);
    input.value = '';
  } else if (input.id === 'voices-import-file') {
    const file = input.files[0];
    if (file) importPack(file);
    input.value = '';
  } else if (input.dataset.limit) {
    // Two radios without a name (a name would be saved with the config), so group them by hand.
    for (const radio of root.querySelectorAll('input[data-limit]')) radio.checked = radio === input;
  }
});

document.getElementById('voices-create').addEventListener('click', (event) => createVoice(event.currentTarget));

/** Refresh when the Announcements tab is shown; stop polling when it is hidden. */
new MutationObserver(() => {
  if (panelVisible()) {
    loadList().then(() => loadDetail());
  } else {
    stopPolling();
  }
}).observe(panel, { attributes: true, attributeFilter: ['hidden'] });

/**
 * Start the voices section. `onChange` runs after anything that alters which
 * voice speaks, so the status panel can redraw.
 */
export async function initVoices({ onChange } = {}) {
  state.onChange = onChange || (() => {});
  const result = await request('GET', '/api/voices');
  if (!result.ok) {
    renderList();
    listEl.innerHTML = '<li class="hint">The voices could not be loaded.</li>';
    fail(result, 'Could not load the voices');
    return;
  }
  document.getElementById('voices-near').value = (result.data.defaults?.near || []).join(', ');
  document.getElementById('voices-km').value = result.data.defaults?.km ?? 20;
  setList(result.data);
}

/** "Add these to the voice's script": the missing clips go on the voice in use. */
export function topUpVoiceInUse(button) {
  return addExtra('topup', button);
}

/** A script part's label in words an owner would use. */
function partLabel(label) {
  const names = {
    pilot: 'Try-out: one whole announcement',
    core: 'Phrases, numbers and train companies',
    'local-mid': 'Your stations (in a list)',
    'local-end': 'Your stations (end of a sentence)',
    reasons: 'Reasons for delays and cancellations',
    'network-mid': 'Rest of Britain (in a list)',
    'network-end': 'Rest of Britain (end of a sentence)',
    redo: 'Clips to record again',
    topup: 'Clips the board has needed',
  };
  return names[label] || label;
}
