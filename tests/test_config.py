"""Config validation, defaults and round-tripping through YAML."""

import pytest
import yaml
from pydantic import ValidationError

from describer.config import Config, ConfigStore, load_config, save_config

EXAMPLE = "config.example.yaml"


def test_example_config_is_valid():
    config = load_config(__import__("pathlib").Path(EXAMPLE))

    assert config.stations[0].crs == "PAD"
    assert config.display.theme == "modern"


def test_crs_is_upper_cased():
    assert Config(stations=[{"crs": "pad"}]).stations[0].crs == "PAD"


def test_poll_interval_floor_is_enforced():
    with pytest.raises(ValidationError):
        Config(stations=[{"crs": "PAD"}], api={"poll_interval": 5})


def test_at_most_two_stations():
    with pytest.raises(ValidationError):
        Config(stations=[{"crs": "PAD"}, {"crs": "RDG"}, {"crs": "OXF"}])


def test_bad_crs_rejected():
    with pytest.raises(ValidationError):
        Config(stations=[{"crs": "PADD"}])


def test_bad_time_rejected():
    with pytest.raises(ValidationError):
        Config(stations=[{"crs": "PAD"}], schedule={"on_time": "25:00"})


def test_incomplete_weekday_override_rejected():
    with pytest.raises(ValidationError):
        Config(
            stations=[{"crs": "PAD"}],
            schedule={"per_weekday": {"mon": {"on_time": "09:00"}}},
        )


def test_save_and_reload_round_trips(tmp_path):
    path = tmp_path / "config.yaml"
    config = Config(stations=[{"crs": "PAD", "mode": "arrivals", "rows": 5}])
    save_config(config, path)

    raw = yaml.safe_load(path.read_text())
    assert raw["stations"][0]["mode"] == "arrivals"
    assert load_config(path) == config


def test_missing_file_falls_back_to_defaults(tmp_path):
    config = load_config(tmp_path / "nope.yaml")

    assert config.stations[0].crs == "PAD"


def test_store_persists_and_reloads(tmp_path):
    path = tmp_path / "config.yaml"
    store = ConfigStore(Config(stations=[{"crs": "PAD"}]), path)
    store.set(Config(stations=[{"crs": "RDG"}]))

    assert path.exists()
    assert store.reload().stations[0].crs == "RDG"


def test_announce_any_needs_both_switches():
    assert Config(stations=[{"crs": "PAD"}]).announce_any
    assert not Config(stations=[{"crs": "PAD", "announce": False}]).announce_any
    assert not Config(stations=[{"crs": "PAD"}], announcements={"enabled": False}).announce_any
