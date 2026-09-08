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
        Config(stations=[{"crs": "PAD"}], sources={"poll_interval": 5})


def test_the_old_api_key_still_loads(tmp_path):
    """v1 config files keep working for one release."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "stations:\n  - crs: PAD\n"
        "api:\n"
        "  base_url: https://example.test/ldbws/\n"
        "  poll_interval: 45\n"
        "  timeout: 7.5\n"
        "  stale_after: 200\n"
    )

    config = load_config(path)

    assert config.sources.rdm.base_url == "https://example.test/ldbws"
    assert config.sources.rdm.timeout == 7.5
    assert config.sources.poll_interval == 45
    assert config.sources.stale_after == 200
    assert config.sources.primary == "rdm"


def test_the_new_sources_key_wins_over_the_old_one(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "stations:\n  - crs: PAD\napi:\n  poll_interval: 45\nsources:\n  poll_interval: 60\n"
    )

    assert load_config(path).sources.poll_interval == 60


def test_a_source_cannot_be_its_own_fallback():
    with pytest.raises(ValidationError):
        Config(stations=[{"crs": "PAD"}], sources={"primary": "rtt", "fallback": "rtt"})


def test_detail_rows_are_bounded():
    with pytest.raises(ValidationError):
        Config(stations=[{"crs": "PAD"}], sources={"rtt": {"detail_rows": 20}})
    assert Config(stations=[{"crs": "PAD"}], sources={"rtt": {"detail_rows": 12}})


def test_failover_can_be_switched_off():
    config = Config(stations=[{"crs": "PAD"}], sources={"fallback": None})

    assert config.sources.fallback is None


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


def test_a_walk_time_of_zero_keeps_every_train():
    station = Config(stations=[{"crs": "RDG"}]).stations[0]

    assert station.walk_time == 0
    assert station.accepts_time(0)
    assert station.accepts_time(-120)


def test_a_walk_time_drops_the_trains_that_cannot_be_reached():
    station = Config(stations=[{"crs": "RDG", "walk_time": 10}]).stations[0]

    assert not station.accepts_time(9 * 60)
    assert station.accepts_time(10 * 60)
    assert station.accepts_time(30 * 60)


def test_a_train_with_no_readable_time_survives_the_walk_time():
    """Dropping it would take it off the board for the wrong reason."""
    station = Config(stations=[{"crs": "RDG", "walk_time": 10}]).stations[0]

    assert station.accepts_time(None)


def test_platforms_are_normalised_and_deduplicated():
    station = Config(stations=[{"crs": "RDG", "platforms": [" 2a ", "7", "2A", ""]}]).stations[0]

    assert station.platforms == ["2A", "7"]


def test_no_platform_filter_accepts_everything():
    station = Config(stations=[{"crs": "RDG"}]).stations[0]

    assert station.accepts_platform("4")
    assert station.accepts_platform(None)


def test_platform_filter_matches_case_insensitively():
    station = Config(stations=[{"crs": "RDG", "platforms": ["2a"]}]).stations[0]

    assert station.accepts_platform("2A")
    assert station.accepts_platform(" 2a ")
    assert not station.accepts_platform("2B")


def test_an_unconfirmed_platform_is_dropped_unless_asked_for():
    strict = Config(stations=[{"crs": "RDG", "platforms": ["7"]}]).stations[0]
    lenient = Config(
        stations=[{"crs": "RDG", "platforms": ["7"], "show_unplatformed": True}]
    ).stations[0]

    assert not strict.accepts_platform(None)
    assert not strict.accepts_platform("  ")
    assert lenient.accepts_platform(None)
    assert lenient.accepts_platform("  ")


def test_theme_colours_default_to_the_stylesheet():
    colours = Config(stations=[{"crs": "PAD"}]).display.themes.modern.colours

    # None everywhere means the config says nothing and the CSS decides.
    assert set(colours.model_dump().values()) == {None}


def test_theme_colours_round_trip_and_are_held_lower_case(tmp_path):
    path = tmp_path / "config.yaml"
    config = Config(
        stations=[{"crs": "PAD"}],
        display={"themes": {"modern": {"colours": {"accent": "#AABBCC"}}}},
    )
    assert config.display.themes.modern.colours.accent == "#aabbcc"

    save_config(config, path)
    raw = yaml.safe_load(path.read_text())
    assert raw["display"]["themes"]["modern"]["colours"]["accent"] == "#aabbcc"
    assert raw["display"]["themes"]["thameslink"]["colours"]["late"] is None
    assert load_config(path) == config


@pytest.mark.parametrize("value", ["red", "#abc", "#12345g", "rgb(1,2,3)", ""])
def test_a_colour_that_is_not_a_six_digit_hex_is_rejected(value):
    with pytest.raises(ValidationError):
        Config(
            stations=[{"crs": "PAD"}],
            display={"themes": {"thameslink": {"colours": {"background": value}}}},
        )
