"""Timing helpers on Service."""

from datetime import datetime

from describer.rail.models import Service, ServiceStatus


def service(**overrides) -> Service:
    return Service(id="s", **overrides)


def test_effective_time_prefers_the_estimate():
    late = service(scheduled_time="14:32", expected_time="14:47", status=ServiceStatus.EXPECTED)

    assert late.effective_time() == "14:47"
    assert service(scheduled_time="14:32", expected_time="On time").effective_time() == "14:32"


def test_seconds_until_counts_forward():
    now = datetime(2024, 5, 14, 14, 30)

    assert service(scheduled_time="14:32").seconds_until(now) == 120


def test_a_train_a_few_minutes_late_stays_today():
    now = datetime(2024, 5, 14, 14, 35)

    assert service(scheduled_time="14:32").seconds_until(now) == -180


def test_a_time_long_past_is_read_as_tomorrow():
    now = datetime(2024, 5, 14, 23, 50)

    assert service(scheduled_time="00:10").seconds_until(now) == 1200


def test_unknown_times_give_nothing():
    assert service().seconds_until(datetime.now()) is None
    assert service(scheduled_time="Delayed").seconds_until(datetime.now()) is None
