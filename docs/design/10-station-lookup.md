# Addendum 10 — Looking a station up by name in /admin

A station card used to want the CRS code and nothing else, which meant
knowing it or looking it up elsewhere. Each card now has a **Station** box
above the code: type a name or a code and it suggests stations, and a pick
fills in the CRS field.

## The list is shipped, not fetched

Neither feed has a station search, and a call per keystroke would be the
wrong thing to spend an allowance on anyway. `web/static/stations.json` holds
every National Rail station with a CRS code — 2,638, 74 KB, one to a line —
and `admin.js` fetches it once from `/static` and searches it in the page, so
the lookup costs nothing and works with no internet. Nothing in the app
rebuilds it.

- **Source.** NaPTAN, the rail area (910), as XML from
  `naptan.api.dft.gov.uk`. The CSV export is smaller (555 KB against 27 MB)
  but has no CRS column; the XML carries it on each stop point's
  `AnnotatedRailRef`. The API refuses `HEAD`.
- **Licence.** Open Government Licence v3.0, which asks for attribution: the
  file carries it in its `licence` field, and so does the README.
- **Rebuild** with `python -m describer.stationlist` (downloads) or
  `--source FILE`, then commit. It is a deliberate act, like recording a
  fixture. It refuses to write fewer than 2,000 stations, so a broken download
  cannot replace the list with a stub.
- **Parsing** (`parse_naptan`): active stop points with a three-letter CRS,
  "Rail Station" / "Railway Station" / "Station" stripped from the name. 29
  codes have more than one stop point — Clapham Junction has five, and SGB is
  both "Smethwick Galton Bridge" and its "High Level" — so each keeps its
  shortest name. Names are NaPTAN's, not Darwin's: "London Kings Cross"
  without the apostrophe, "Abbey Wood (London)" for ABW beside a separate
  ABX "Abbey Wood".
- `tests/fixtures/naptan_910_sample.xml` is ten stop points cut from the real
  file, with every locality list but the first removed (Paddington's alone was
  3,397 `NptgLocalityRef`s and 530 KB). `test_stationlist.py` also checks the
  committed list's shape.

## The box

- **The CRS field is still what is saved.** The Station box has no `name`, so
  `readForm`'s sweep never sees it, and `readStationList` reads only the
  fields it names. A pick writes the code into the CRS field and dispatches
  `input` and `change` from there, exactly as typing it would: the page marks
  itself unsaved and a profile's editor commits its draft.
- **The box's own `input` and `change` stop at the box.** Searching changes
  nothing that is saved, and the form marks itself unsaved on any `input`
  that reaches it.
- It always shows the name of whatever code the CRS field holds. Leaving it
  without a pick, or pressing Escape, puts that name back. A three-letter code
  the list does not know is flagged under the field and still saved: the list
  is NaPTAN's, and Darwin knows codes it does not (SPX, for one).
- **Ranking** (`searchStations`): the code itself, then names starting with
  the text, then codes starting with it (under three letters), then every
  typed word starting a word of the name ("lon bri"), then the text anywhere.
  Shorter names first within each, so Sydenham precedes Sydenham Hill. A lone
  "x" is "cross". Eight suggestions at most.
- A combobox by ARIA's pattern: `aria-expanded`, `aria-controls`,
  `aria-activedescendant`, arrows to move, Enter to pick — never to submit the
  form from here — and a suggestion chosen on `pointerdown`, since a click
  would blur the box first and close the list under the pointer.
- `stationCard` wires it, so the profile editor's cards have it too. Each
  listbox gets its own id.
