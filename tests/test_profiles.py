"""Which board is on the screen at this hour, and what a profile may change."""

from datetime import datetime, time

import pytest
from pydantic import ValidationError

from describer.config import Config, ConfigStore
from describer.profiles import (
    active_profile,
    in_window,
    next_change,
    profile_matches,
    resolve,
)

# 2024-05-13 is a Monday, so the weekday arithmetic below is readable.
MONDAY_0800 = datetime(2024, 5, 13, 8, 0)
MONDAY_1200 = datetime(2024, 5, 13, 12, 0)
MONDAY_1800 = datetime(2024, 5, 13, 18, 0)
SATURDAY_0800 = datetime(2024, 5, 18, 8, 0)

MORNING = {
    "name": "Morning rush",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "start": "06:30",
    "end": "09:30",
    "stations": [{"crs": "ABW", "mode": "departures", "rows": 10}],
    "display": {"theme": "thameslink"},
}
EVENING = {
    "name": "Evening",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "start": "16:30",
    "end": "19:30",
    "stations": [{"crs": "LBG"}],
    "announcements": {"enabled": False},
}


def config_with(*entries, enabled: bool = True, **overrides) -> Config:
    base = {
        "stations": [{"crs": "PAD", "rows": 8}],
        "display": {"theme": "modern", "clock": False},
        "profiles": {"enabled": enabled, "entries": list(entries)},
    }
    return Config.model_validate({**base, **overrides})


# -- the window arithmetic --------------------------------------------------


@pytest.mark.parametrize(
    "on,off,now,expected",
    [
        ("06:30", "09:30", "08:00", True),
        ("06:30", "09:30", "09:30", False),  # the end is exclusive
        ("06:30", "09:30", "06:30", True),  # the start is not
        ("06:30", "09:30", "10:00", False),
        ("22:00", "02:00", "23:30", True),  # wraps midnight
        ("22:00", "02:00", "01:00", True),
        ("22:00", "02:00", "12:00", False),
        ("00:00", "00:00", "03:00", True),  # start == end is all day
    ],
)
def test_in_window(on, off, now, expected):
    parse = lambda text: time(*(int(part) for part in text.split(":")))  # noqa: E731
    assert in_window(parse(on), parse(off), parse(now)) is expected


def test_a_profile_only_claims_its_own_weekdays():
    profile = config_with(MORNING).profiles.entries[0]

    assert profile_matches(profile, MONDAY_0800)
    assert not profile_matches(profile, SATURDAY_0800)


# -- which one is in force --------------------------------------------------


def test_the_first_matching_profile_wins():
    early = {**MORNING, "name": "Early", "start": "06:00", "end": "12:00"}
    config = config_with(MORNING, early)

    assert active_profile(config, MONDAY_0800).name == "Morning rush"


def test_no_match_falls_through_to_the_base_config():
    config = config_with(MORNING, EVENING)

    assert active_profile(config, MONDAY_1200) is None
    # Not merely equal: an unprofiled board does no work at all.
    assert resolve(config, MONDAY_1200) is config


def test_a_switched_off_profiles_block_is_ignored():
    config = config_with(MORNING, enabled=False)

    assert active_profile(config, MONDAY_0800) is None


def test_a_forced_profile_ignores_the_clock_and_the_switch():
    config = config_with(MORNING, EVENING, enabled=False)

    assert active_profile(config, MONDAY_1200, forced="Evening").name == "Evening"


def test_a_forced_profile_that_no_longer_exists_falls_back():
    """Deleting a pinned profile must not take the board down with it."""
    config = config_with(MORNING)

    assert active_profile(config, MONDAY_0800, forced="Gone") is None


# -- what the merge does ----------------------------------------------------


def test_stations_are_replaced_wholesale():
    config = config_with(MORNING, stations=[{"crs": "PAD"}, {"crs": "RDG"}])

    resolved = resolve(config, MONDAY_0800)

    assert [station.crs for station in resolved.stations] == ["ABW"]
    assert resolved.stations[0].rows == 10


def test_an_unset_key_keeps_the_base_value():
    config = config_with(MORNING)

    resolved = resolve(config, MONDAY_0800)

    assert resolved.display.theme == "thameslink"  # overridden
    assert resolved.display.clock is False  # not mentioned, so the base stands
    assert resolved.sources.poll_interval == config.sources.poll_interval


def test_a_theme_option_merges_key_by_key():
    profile = {
        **MORNING,
        "display": {"theme": "crt", "themes": {"crt": {"phosphor": "green"}}},
    }
    config = config_with(profile, display={"themes": {"crt": {"scanlines": False}}})

    resolved = resolve(config, MONDAY_0800)

    assert resolved.display.themes.crt.phosphor == "green"
    assert resolved.display.themes.crt.scanlines is False


def test_announcements_can_be_silenced_for_a_period():
    config = config_with(EVENING)

    resolved = resolve(config, MONDAY_1800)

    assert resolved.announcements.enabled is False
    assert resolved.announcements.voice == config.announcements.voice


def test_a_resolved_config_carries_no_profiles():
    """Nothing downstream may resolve a second time."""
    resolved = resolve(config_with(MORNING), MONDAY_0800)

    assert resolved.profiles.entries == []


# -- validation -------------------------------------------------------------


def test_a_profile_naming_a_theme_that_does_not_exist_is_rejected():
    with pytest.raises(ValidationError, match="not-a-theme"):
        config_with({**MORNING, "display": {"theme": "not-a-theme"}})


def test_a_bad_theme_option_is_caught_by_the_merge_and_named():
    """A theme's options are held loosely, so the merged board is what proves them.

    A profile is applied hours after it is saved; finding this out at 06:30 is
    not an option.
    """
    with pytest.raises(ValidationError, match="Morning rush"):
        config_with({**MORNING, "display": {"themes": {"crt": {"phosphor": "purple"}}}})


def test_duplicate_profile_names_are_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        config_with(MORNING, {**MORNING, "start": "10:00", "end": "11:00"})


def test_days_are_normalised_into_week_order():
    config = config_with({**MORNING, "days": ["FRI", "mon", "fri"]})

    assert config.profiles.entries[0].days == ["mon", "fri"]


# -- what happens next ------------------------------------------------------


def test_next_change_names_the_profile_taking_over():
    config = config_with(MORNING, EVENING)

    when, name = next_change(config, MONDAY_0800)

    assert (when.hour, when.minute) == (9, 30)
    assert name is None  # the base config holds the middle of the day

    when, name = next_change(config, MONDAY_1200)
    assert (when.hour, when.minute) == (16, 30)
    assert name == "Evening"


def test_next_change_skips_the_weekend_a_profile_does_not_cover():
    config = config_with(MORNING)

    when, name = next_change(config, datetime(2024, 5, 17, 12, 0))  # Friday noon

    assert when.date() == datetime(2024, 5, 20).date()  # the following Monday
    assert name == "Morning rush"


def test_nothing_changes_while_a_profile_is_pinned():
    config = config_with(MORNING, EVENING)

    assert next_change(config, MONDAY_0800, forced="Evening") is None


# -- the store --------------------------------------------------------------


def test_the_store_serves_the_file_raw_and_the_board_resolved(tmp_path):
    """The admin page edits the file; the board runs the resolution of it."""
    store = ConfigStore(config_with(MORNING), tmp_path / "config.yaml")

    assert store.get().stations[0].crs == "PAD"
    assert store.active(MONDAY_0800).stations[0].crs == "ABW"
    assert store.active(MONDAY_1200).stations[0].crs == "PAD"


def test_the_resolution_is_cached_until_the_config_changes(tmp_path):
    store = ConfigStore(config_with(MORNING), tmp_path / "config.yaml")

    first = store.active(MONDAY_0800)
    assert store.active(MONDAY_0800) is first

    store.set(config_with(MORNING, stations=[{"crs": "RDG"}]), persist=False)
    assert store.active(MONDAY_0800) is not first


def test_forcing_a_profile_is_held_in_memory_only(tmp_path):
    path = tmp_path / "config.yaml"
    store = ConfigStore(config_with(MORNING, EVENING), path)

    store.force_profile("Evening")

    assert store.active(MONDAY_1200).stations[0].crs == "LBG"
    assert not path.exists()  # forcing never writes

    store.force_profile(None)
    assert store.active(MONDAY_1200).stations[0].crs == "PAD"
