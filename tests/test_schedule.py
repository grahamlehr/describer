"""Display on/off windows."""

from datetime import datetime

from describer.config import ScheduleConfig
from describer.schedule import is_display_on, window_for

MONDAY_NOON = datetime(2024, 5, 13, 12, 0)
MONDAY_NIGHT = datetime(2024, 5, 13, 23, 30)
SUNDAY_NOON = datetime(2024, 5, 19, 12, 0)


def test_disabled_schedule_is_always_on():
    assert is_display_on(ScheduleConfig(enabled=False), MONDAY_NIGHT)


def test_daytime_window():
    config = ScheduleConfig(enabled=True, on_time="06:00", off_time="23:00")

    assert is_display_on(config, MONDAY_NOON)
    assert not is_display_on(config, MONDAY_NIGHT)
    assert not is_display_on(config, datetime(2024, 5, 13, 5, 59))


def test_window_wrapping_midnight():
    config = ScheduleConfig(enabled=True, on_time="23:00", off_time="06:00")

    assert is_display_on(config, MONDAY_NIGHT)
    assert is_display_on(config, datetime(2024, 5, 13, 2, 0))
    assert not is_display_on(config, MONDAY_NOON)


def test_per_weekday_override():
    config = ScheduleConfig(
        enabled=True,
        on_time="06:00",
        off_time="23:00",
        per_weekday={"sun": None, "mon": {"on_time": "10:00", "off_time": "12:00"}},
    )

    assert not is_display_on(config, SUNDAY_NOON)
    assert window_for(config, SUNDAY_NOON) is None
    assert is_display_on(config, datetime(2024, 5, 13, 11, 0))
    assert not is_display_on(config, datetime(2024, 5, 13, 13, 0))


def test_equal_times_mean_always_on():
    config = ScheduleConfig(enabled=True, on_time="06:00", off_time="06:00")

    assert is_display_on(config, MONDAY_NIGHT)


def test_display_mode_command_forces_the_configured_resolution():
    from describer.config import DisplayConfig
    from describer.schedule import display_mode_command

    assert display_mode_command(DisplayConfig(resolution="1080p")) == [
        "wlr-randr",
        "--output",
        "HDMI-A-1",
        "--mode",
        "1920x1080",
    ]
    assert display_mode_command(DisplayConfig(resolution="720p"))[-1] == "1280x720"
    assert display_mode_command(DisplayConfig(resolution="auto"))[-1] == "--preferred"


async def test_set_display_mode_is_a_no_op_without_wlr_randr(monkeypatch):
    from describer import schedule
    from describer.config import DisplayConfig

    monkeypatch.setattr(schedule.shutil, "which", lambda _name: None)

    assert await schedule.set_display_mode(DisplayConfig(resolution="1080p")) is True
