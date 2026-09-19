/**
 * The station lookup, shared by /admin and /setup: a combobox over every
 * station NaPTAN knows. (Addendum 10.) Nothing here touches the page's own
 * form state; a pick writes the code into the CRS field and dispatches
 * `input` and `change` from it, exactly as typing it would, and the caller
 * decides what that means.
 */

/**
 * Every station NaPTAN knows, as [code, name], committed as
 * /static/stations.json by `python -m describer.stationlist` and searched
 * here in the page, so a keystroke costs no request and the page works with
 * no internet. The CRS field stays the value that is saved; the lookup only
 * ever fills it in.
 */
const MAX_SUGGESTIONS = 8;
/** Station name by code, filled once the list arrives. */
export const stationNames = new Map();
/** [lat, lon] by code, when NaPTAN has a fix for it (about 2,630 of 2,638 do). */
export const stationCoords = new Map();
export const stationsReady = fetch('/static/stations.json')
  .then((response) => (response.ok ? response.json() : null))
  // Rows are [crs, name, lat, lon]; lat/lon are read by position; a row with
  // no fix at all (a handful of NaPTAN entries) is JS's ordinary undefined.
  .then((data) => (data?.stations || []).map(([crs, name, lat, lon]) => indexStation(crs, name, lat, lon)))
  .catch(() => []);
let lookupIds = 0;

/** Lower case, no apostrophes or full stops, "&" as "and", words split on anything else. */
function normaliseName(text) {
  return String(text)
    .toLowerCase()
    .replace(/['’.]/g, '')
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function indexStation(crs, name, lat, lon) {
  stationNames.set(crs, name);
  if (lat != null && lon != null) stationCoords.set(crs, [lat, lon]);
  const key = normaliseName(name);
  return { crs, name, key, words: key.split(' ') };
}

/**
 * The best few stations for what has been typed. In order: the code itself
 * ("syd"), a name that starts with it ("sydenham h"), a code that starts with
 * it, every typed word starting a word of the name ("lon bri", "cross"), and
 * last the text anywhere in the name. Shorter names first within each, so
 * "Sydenham" comes before "Sydenham Hill". A lone "x" is "cross", as it is
 * written on half the signs in London: "kings x".
 */
export function searchStations(list, query) {
  const text = normaliseName(query)
    .split(' ')
    .map((word) => (word === 'x' ? 'cross' : word))
    .join(' ');
  if (!text) return [];
  const words = text.split(' ');
  const code = text.replace(/ /g, '').toUpperCase();
  const scored = [];
  for (const station of list) {
    let score;
    if (code.length === 3 && station.crs === code) score = 0;
    else if (station.key.startsWith(text)) score = 1;
    else if (code.length < 3 && words.length === 1 && station.crs.startsWith(code)) score = 2;
    else if (words.every((word) => station.words.some((part) => part.startsWith(word)))) score = 3;
    else if (station.key.includes(text)) score = 4;
    else continue;
    scored.push([score, station]);
  }
  scored.sort(([a, s], [b, t]) => a - b || s.name.length - t.name.length || s.name.localeCompare(t.name));
  return scored.slice(0, MAX_SUGGESTIONS).map(([, station]) => station);
}

/** Whether the chosen CRS has a forecast location in the station list — the
 *  "Forecast location" details block's own hint, independent of the name
 *  lookup's "not in the list" warning above it. */
function showCoordsNote(crs) {
  if (!/^[A-Z]{3}$/.test(crs)) return '';
  return stationCoords.has(crs)
    ? 'Forecast location from the station list (NaPTAN); the fields above override it.'
    : 'No coordinates for this code in the station list — no forecast for this board unless set above.';
}

/**
 * A combobox over the station list, for one station. Typing a name or a
 * code suggests stations; arrows and Enter, or a click, put the chosen code in
 * the CRS field as if it had been typed there. The box itself shows the name
 * of whatever code the CRS field holds, and a code with no name in the list is
 * flagged, not refused: the list is NaPTAN's, not Darwin's.
 */
export function wireStationLookup({ lookup, crsField, listbox, note, coordsNote }) {
  const id = `station-lookup-${(lookupIds += 1)}`;
  listbox.id = id;
  lookup.setAttribute('aria-controls', id);
  let matches = [];
  let active = -1;

  const showCurrent = () => {
    const crs = crsField.value.trim().toUpperCase();
    const name = stationNames.get(crs);
    lookup.value = name || '';
    const unknown = stationNames.size > 0 && /^[A-Z]{3}$/.test(crs) && !name;
    note.hidden = !unknown;
    note.textContent = unknown ? 'Not in the station list: check the code.' : '';
    if (coordsNote) coordsNote.textContent = showCoordsNote(crs);
  };

  const highlight = (index) => {
    active = index;
    Array.from(listbox.children).forEach((option, i) => {
      option.setAttribute('aria-selected', String(i === index));
    });
    if (index < 0) {
      lookup.removeAttribute('aria-activedescendant');
      return;
    }
    const option = listbox.children[index];
    lookup.setAttribute('aria-activedescendant', option.id);
    option.scrollIntoView({ block: 'nearest' });
  };

  const close = () => {
    listbox.hidden = true;
    lookup.setAttribute('aria-expanded', 'false');
    highlight(-1);
  };

  const pick = (station) => {
    crsField.value = station.crs;
    // As if the code had been typed: the page marks itself unsaved, and a
    // profile's editor takes the change into its draft.
    crsField.dispatchEvent(new Event('input', { bubbles: true }));
    crsField.dispatchEvent(new Event('change', { bubbles: true }));
    close();
  };

  const suggest = async () => {
    const list = await stationsReady;
    matches = searchStations(list, lookup.value);
    listbox.textContent = '';
    if (!lookup.value.trim()) {
      close();
      return;
    }
    if (!matches.length) {
      const none = document.createElement('li');
      none.className = 'none';
      none.textContent = list.length ? 'No station matches' : 'The station list did not load';
      listbox.append(none);
    }
    matches.forEach((station, i) => {
      const option = document.createElement('li');
      option.id = `${id}-${i}`;
      option.setAttribute('role', 'option');
      const name = document.createElement('span');
      name.textContent = station.name;
      const code = document.createElement('span');
      code.className = 'code';
      code.textContent = station.crs;
      option.append(name, code);
      // pointerdown, not click: a click would blur the box first and close it.
      option.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        pick(station);
      });
      listbox.append(option);
    });
    listbox.hidden = false;
    lookup.setAttribute('aria-expanded', 'true');
    highlight(matches.length ? 0 : -1);
  };

  // Searching changes nothing that is saved, so the box's own events stop
  // here rather than reaching the form's listeners, which would mark the page
  // unsaved. Only a pick does that, through the CRS field.
  for (const type of ['input', 'change']) {
    lookup.addEventListener(type, (event) => event.stopPropagation());
  }
  lookup.addEventListener('input', suggest);
  // Focusing selects the name, so typing replaces it; after the click's own
  // caret placement, or the selection would be undone.
  lookup.addEventListener('focus', () => setTimeout(() => lookup.select()));
  lookup.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (listbox.hidden) {
        suggest();
        return;
      }
      if (!matches.length) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      highlight((active + step + matches.length) % matches.length);
    } else if (event.key === 'Enter') {
      // Enter here means "this one", never "save the form".
      event.preventDefault();
      if (!listbox.hidden && active >= 0) pick(matches[active]);
    } else if (event.key === 'Escape' && !listbox.hidden) {
      event.preventDefault();
      close();
      showCurrent();
    }
  });
  // Leaving without a pick puts back the name of the code that is actually set.
  lookup.addEventListener('blur', () => {
    close();
    showCurrent();
  });
  crsField.addEventListener('input', showCurrent);
  stationsReady.then(showCurrent);
}
