"""What gets announced, and how often."""

from datetime import datetime, timedelta

import pytest

from describer.announce.scheduler import FORGET_AFTER, AnnouncementScheduler
from describer.config import Config
from describer.rail.models import Board, Service, ServiceStatus


class RecordingEngine:
    """Stands in for Piper: records what would have been spoken."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak(self, text: str) -> None:
        self.spoken.append(text)


def board_with(*services: Service, source: str = "rdm") -> Board:
    return Board(
        crs="PAD",
        name="London Paddington",
        mode="departures",
        source=source,
        services=list(services),
    )


def service_at(offset_minutes: float, **overrides) -> Service:
    """A service whose effective time is ``offset_minutes`` from now."""
    when = datetime.now().astimezone() + timedelta(minutes=offset_minutes)
    hhmm = when.strftime("%H:%M")
    # An "expected" service carries a real estimate, exactly as Darwin sends it.
    expected = hhmm if overrides.get("status") is ServiceStatus.EXPECTED else "On time"
    defaults = {
        "id": overrides.pop("id", "svc1"),
        "scheduled_time": hhmm,
        "expected_time": expected,
        "destination": "Bristol Temple Meads",
        "operator": "Great Western Railway",
        "platform": "9",
        "status": ServiceStatus.ON_TIME,
    }
    return Service(**{**defaults, **overrides})


@pytest.fixture
def scheduler() -> tuple[AnnouncementScheduler, RecordingEngine]:
    engine = RecordingEngine()
    return AnnouncementScheduler(engine), engine


async def drain(scheduler: AnnouncementScheduler) -> None:
    """Speak everything queued, without running the background worker."""
    while not scheduler._queue.empty():
        await scheduler.say(scheduler._queue.get_nowait())


async def test_announces_a_service_inside_the_lead_time(scheduler):
    announcer, engine = scheduler
    config = Config(stations=[{"crs": "PAD"}])

    await announcer.on_boards([board_with(service_at(1))], config)
    await drain(announcer)

    assert len(engine.spoken) == 1
    assert engine.spoken[0].startswith("The next train to arrive at platform nine")


async def test_ignores_a_service_beyond_the_lead_time(scheduler):
    announcer, engine = scheduler

    await announcer.on_boards([board_with(service_at(30))], Config(stations=[{"crs": "PAD"}]))
    await drain(announcer)

    assert engine.spoken == []


async def test_a_walk_time_widens_the_lead_so_the_call_still_comes(scheduler):
    """The train leaves the board at the walk time, so that is the last call."""
    announcer, engine = scheduler
    config = Config(stations=[{"crs": "PAD", "walk_time": 10}])

    await announcer.on_boards([board_with(service_at(9.5))], config)
    await drain(announcer)

    assert len(engine.spoken) == 1


async def test_a_walk_time_does_not_announce_the_whole_board(scheduler):
    announcer, engine = scheduler
    config = Config(stations=[{"crs": "PAD", "walk_time": 10}])

    await announcer.on_boards([board_with(service_at(30))], config)
    await drain(announcer)

    assert engine.spoken == []


async def test_each_service_is_announced_once(scheduler):
    announcer, engine = scheduler
    config = Config(stations=[{"crs": "PAD"}])
    boards = [board_with(service_at(1))]

    for _ in range(3):
        await announcer.on_boards(boards, config)
        await drain(announcer)

    assert len(engine.spoken) == 1


async def test_cancellation_is_announced_instead_of_arrival(scheduler):
    announcer, engine = scheduler
    cancelled = service_at(1, status=ServiceStatus.CANCELLED, expected_time="Cancelled")

    await announcer.on_boards([board_with(cancelled)], Config(stations=[{"crs": "PAD"}]))
    await drain(announcer)

    assert len(engine.spoken) == 1
    assert "has been cancelled" in engine.spoken[0]


async def test_delay_and_arrival_are_separate_events(scheduler):
    announcer, engine = scheduler
    delayed = service_at(1, status=ServiceStatus.EXPECTED, delay_minutes=9)

    await announcer.on_boards([board_with(delayed)], Config(stations=[{"crs": "PAD"}]))
    await drain(announcer)

    assert len(engine.spoken) == 2
    assert any("is delayed by approximately nine minutes" in text for text in engine.spoken)
    assert any(text.startswith("The next train") for text in engine.spoken)


async def test_station_can_opt_out(scheduler):
    announcer, engine = scheduler
    config = Config(stations=[{"crs": "PAD", "announce": False}])

    await announcer.on_boards([board_with(service_at(1))], config)
    await drain(announcer)

    assert engine.spoken == []


async def test_global_switch_silences_everything(scheduler):
    announcer, engine = scheduler
    config = Config(stations=[{"crs": "PAD"}], announcements={"enabled": False})

    await announcer.on_boards([board_with(service_at(1))], config)
    await drain(announcer)

    assert engine.spoken == []


async def test_departed_services_are_forgotten_after_a_while(scheduler):
    """A train that left the board is remembered for a while, then dropped."""
    announcer, _ = scheduler
    config = Config(stations=[{"crs": "PAD"}])

    await announcer.on_boards([board_with(service_at(1))], config)
    assert announcer._announced
    await announcer.on_boards([board_with()], config)
    # Off the board is not gone: a profile can take a station away at 09:30 and
    # bring it back at 16:30, and its trains must not be announced twice.
    assert announcer._announced

    # Age everything past the horizon and the next pass lets it go.
    stale = datetime.now().astimezone() - FORGET_AFTER - timedelta(seconds=1)
    announcer._announced = dict.fromkeys(announcer._announced, stale)
    await announcer.on_boards([board_with()], config)

    assert announcer._announced == {}


async def test_a_station_that_comes_back_is_not_announced_twice(scheduler):
    """The case profiles make daily: a station leaves the board and returns."""
    announcer, engine = scheduler
    config = Config(stations=[{"crs": "PAD"}])
    train = service_at(1)

    await announcer.on_boards([board_with(train)], config)
    await drain(announcer)
    # The midday profile has no PAD board at all.
    await announcer.on_boards([], config)
    # ... and the evening one brings it back.
    await announcer.on_boards([board_with(train)], config)
    await drain(announcer)

    assert len(engine.spoken) == 1


async def test_a_source_switch_does_not_re_announce_a_train(scheduler):
    """The two feeds number the same train differently; the announcer must not care."""
    announcer, engine = scheduler
    config = Config(stations=[{"crs": "PAD"}])
    from_rdm = board_with(service_at(1, id="aaa111"))
    # The same 14:32 to Bristol, now carrying an RTT identifier.
    from_rtt = board_with(
        service_at(1, id="rtt:W12345:2024-05-14"),
        source="rtt",
    )

    await announcer.on_boards([from_rdm], config)
    await drain(announcer)
    await announcer.on_boards([from_rtt], config)
    await drain(announcer)

    assert len(engine.spoken) == 1
