"""The station list behind /admin's CRS lookup, and the file it is built into."""

import json
import re
from datetime import date

from describer.stationlist import LICENCE, OUTPUT, coordinates_for, main, parse_naptan, render
from tests.conftest import FIXTURES

SAMPLE = FIXTURES / "naptan_910_sample.xml"


def test_parses_active_stations_with_a_code_by_name():
    """Ten stop points cut from the real 910 file, awkward ones included."""
    assert parse_naptan(SAMPLE) == [
        ("ASD", "Ashley Down", 51.4787, -2.5767),  # a name with no "Rail Station" to strip
        ("BIA", "Bishop Auckland", 54.6572, -1.6777),  # "West Station" shares the code
        ("LBG", "London Bridge", 51.505, -0.0861),  # two stop points, one station
        ("PAD", "London Paddington", 51.516, -0.1762),
        ("SGB", "Smethwick Galton Bridge", 52.5018, -1.9805),  # not "High Level"
    ]


def test_inactive_and_codeless_stop_points_are_left_out():
    codes = [row[0] for row in parse_naptan(SAMPLE)]

    assert "AGR" not in codes  # Angel Road, closed and marked inactive
    assert len(codes) == len(set(codes))


def test_render_is_one_station_a_line_and_valid_json():
    text = render(
        [("PAD", "London Paddington", 51.5164, -0.1762), ("SYD", "Sydenham", 51.4267, -0.0524)],
        date(2026, 9, 13),
    )

    data = json.loads(text)
    assert data["stations"] == [
        ["PAD", "London Paddington", 51.5164, -0.1762],
        ["SYD", "Sydenham", 51.4267, -0.0524],
    ]
    assert data["generated"] == "2026-09-13"
    assert data["licence"] == LICENCE
    assert '    ["SYD", "Sydenham", 51.4267, -0.0524]\n' in text


def test_main_rebuilds_from_a_file(tmp_path):
    output = tmp_path / "stations.json"

    assert main(["--source", str(SAMPLE), "--output", str(output), "--minimum", "1"]) == 0

    assert [row[0] for row in json.loads(output.read_text())["stations"]][:2] == ["ASD", "BIA"]


def test_main_refuses_to_write_a_short_list(tmp_path):
    """A broken download must not replace the real list with a stub."""
    output = tmp_path / "stations.json"

    assert main(["--source", str(SAMPLE), "--output", str(output)]) == 1
    assert not output.exists()


def test_the_committed_list_is_whole_and_well_formed():
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    stations = {row[0]: row[1] for row in data["stations"]}

    assert len(data["stations"]) > 2500
    assert len(stations) == len(data["stations"])  # no code twice
    assert all(re.fullmatch(r"[A-Z]{3}", crs) for crs in stations)
    assert all(name and not name.endswith("Station") for name in stations.values())
    assert data["licence"] == LICENCE  # the OGL asks for the attribution
    assert stations["PAD"] == "London Paddington"
    assert stations["SYD"] == "Sydenham"
    assert stations["NBC"] == "New Beckenham"
    assert all(len(row) == 4 for row in data["stations"])  # crs, name, lat, lon


def test_coordinates_come_from_the_committed_list(monkeypatch):
    from describer import stationlist

    monkeypatch.setattr(stationlist, "OUTPUT", SAMPLE.parent / "naptan_910_sample.xml")
    stationlist._coordinates.cache_clear()
    try:
        # Not JSON, so the lookup degrades to "nothing known" rather than raising.
        assert stationlist.coordinates_for("PAD") is None
    finally:
        stationlist._coordinates.cache_clear()

    monkeypatch.setattr(stationlist, "OUTPUT", OUTPUT)
    stationlist._coordinates.cache_clear()
    try:
        assert coordinates_for("pad") == (51.516, -0.1762)  # lower case: it upper-cases
        assert coordinates_for("ABX") is None  # one of the eleven NaPTAN gives no fix
    finally:
        stationlist._coordinates.cache_clear()
