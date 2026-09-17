"""The station list behind the admin page's CRS lookup.

Every National Rail station NaPTAN knows, as ``[crs, name]`` pairs, is
committed as ``web/static/stations.json`` and searched in the browser, so a
keystroke in /admin costs no request and the page works with no internet.
Nothing in the app fetches or rebuilds it. Refreshing it is a deliberate
act, run by hand when stations open or close::

    python -m describer.stationlist                   # download and rebuild
    python -m describer.stationlist --source 910.xml  # rebuild from a file

The source is NaPTAN's rail area (910) in XML: the CSV export has no CRS
column, while the XML carries it on each stop point's ``AnnotatedRailRef``.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import re
import shutil
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import BinaryIO

log = logging.getLogger(__name__)

NAPTAN_URL = "https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=xml&atcoAreaCodes=910"
OUTPUT = Path(__file__).parent / "web" / "static" / "stations.json"
SOURCE = "NaPTAN rail stations (area 910), Department for Transport"
LICENCE = "Contains public sector information licensed under the Open Government Licence v3.0."
#: Fewer than this and the download was broken, not the network shrunk.
MINIMUM = 2000

_NS = "{http://www.naptan.org.uk/}"
_CRS = re.compile(r"[A-Z]{3}")
#: "Aberdare Rail Station", "Bishop Auckland West Station": the board says the name.
_SUFFIX = re.compile(r"\s+(?:Rail(?:way)?\s+)?Station$", re.IGNORECASE)


#: (crs, name, latitude, longitude); coordinates are None when NaPTAN gave none.
StationRow = tuple[str, str, float | None, float | None]


def parse_naptan(source: str | Path | BinaryIO) -> list[StationRow]:
    """``(crs, name, lat, lon)`` for every active stop point carrying a CRS.

    Several stop points can share a code — Clapham Junction has five, one per
    group of platforms, and SGB is both "Smethwick Galton Bridge" and its
    "High Level" — so each code keeps its shortest name, which is the
    station's own rather than one part of it, and the coordinates that came
    with that name rather than one of the others'. Coordinates are rounded to
    4 dp: plenty for a forecast, and it keeps the file diffing readably.
    """
    rows: dict[str, StationRow] = {}
    for _, element in ET.iterparse(source, events=("end",)):
        if element.tag != f"{_NS}StopPoint":
            continue
        try:
            if element.get("Status", "active") != "active":
                continue
            crs = (element.findtext(f".//{_NS}CrsRef") or "").strip().upper()
            name = (
                element.findtext(f"{_NS}Descriptor/{_NS}CommonName")
                or element.findtext(f".//{_NS}StationName")
                or ""
            )
            name = _SUFFIX.sub("", name.strip())
            if not _CRS.fullmatch(crs) or not name:
                continue
            held = rows.get(crs)
            if held is not None and (len(held[1]), held[1]) <= (len(name), name):
                continue
            lat = element.findtext(f".//{_NS}Latitude")
            lon = element.findtext(f".//{_NS}Longitude")
            latitude = round(float(lat), 4) if lat else None
            longitude = round(float(lon), 4) if lon else None
            rows[crs] = (crs, name, latitude, longitude)
        finally:
            # The file is 27 MB; keep only the (empty) stop points in memory.
            element.clear()
    return sorted(rows.values(), key=lambda row: (row[1].lower(), row[0]))


def render(stations: list[StationRow], generated: date) -> str:
    """The JSON file, one station to a line so a refresh diffs readably."""
    head = {"source": SOURCE, "licence": LICENCE, "generated": generated.isoformat()}
    lines = [f"  {json.dumps(key)}: {json.dumps(value)}," for key, value in head.items()]
    body = ",\n".join(f"    {json.dumps(list(row), ensure_ascii=False)}" for row in stations)
    return "{\n" + "\n".join(lines) + '\n  "stations": [\n' + body + "\n  ]\n}\n"


@functools.lru_cache(maxsize=1)
def _coordinates() -> dict[str, tuple[float, float]]:
    """CRS -> (lat, lon) from the committed station list. Loaded once, lazily.

    A backend concern, not an admin one: /admin's own lookup reads the JSON
    file straight from the browser, but forecasts are fetched from here, so
    the list has one home either way.
    """
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    result: dict[str, tuple[float, float]] = {}
    for row in data.get("stations", []):
        if len(row) >= 4 and row[2] is not None and row[3] is not None:
            result[row[0]] = (float(row[2]), float(row[3]))
    return result


def coordinates_for(crs: str) -> tuple[float, float] | None:
    """A station's (lat, lon) from the committed list, or None if unknown."""
    return _coordinates().get(crs.upper())


def _download(url: str, into: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "describer/1.0"})
    with urllib.request.urlopen(request, timeout=300) as response, into.open("wb") as out:
        shutil.copyfileobj(response, out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, help="a NaPTAN 910 XML file (default: download)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--minimum", type=int, default=MINIMUM, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.source:
        stations = parse_naptan(args.source)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "naptan-910.xml"
            log.info("Downloading %s", NAPTAN_URL)
            _download(NAPTAN_URL, path)
            stations = parse_naptan(path)

    if len(stations) < args.minimum:
        log.error("Only %d stations parsed; not writing %s", len(stations), args.output)
        return 1
    args.output.write_text(render(stations, date.today()), encoding="utf-8")
    log.info("Wrote %d stations to %s", len(stations), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
