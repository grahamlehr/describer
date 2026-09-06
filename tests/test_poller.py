"""Polling, failure handling and staleness."""

from datetime import datetime, timedelta

import pytest

from describer.config import Config, ConfigStore
from describer.rail import sources as sources_module
from describer.rail.ldbws import RailApiError, parse_board
from describer.rail.poller import Poller


class FakeClient:
    """Replaces the HTTP client; can be told to fail."""

    def __init__(self, board, *_args, **_kwargs):
        self.board = board
        self.fail = False
        self.calls: list[tuple[str, str]] = []

    async def fetch_board(self, crs: str, mode: str = "departures"):
        self.calls.append((crs, mode))
        if self.fail:
            raise RailApiError("HTTP 503 for PAD")
        return self.board.model_copy(deep=True)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def poller(monkeypatch, rdm_credentials, departures_payload, tmp_path):
    board = parse_board(departures_payload, "PAD", "departures")
    fake = FakeClient(board)
    monkeypatch.setattr(sources_module, "LdbwsClient", lambda *a, **k: fake)
    # Failover has its own tests; here a failure must stay a failure.
    config = Config(stations=[{"crs": "PAD"}], sources={"fallback": None})
    store = ConfigStore(config, tmp_path / "config.yaml")
    instance = Poller(store)
    return instance, fake


async def test_successful_poll_fills_the_board(poller):
    instance, fake = poller

    await instance._poll_slot(0, instance._store.get())
    board = instance.boards()[0]

    assert fake.calls == [("PAD", "departures")]
    assert board.name == "London Paddington"
    assert len(board.services) == 4
    assert board.source == "rdm"
    assert not board.stale


async def test_board_is_empty_and_stale_before_the_first_fetch(poller):
    instance, _ = poller
    board = instance.boards()[0]

    assert board.stale
    assert board.error == "Waiting for first fetch"
    assert board.services == []


async def test_a_fresh_board_survives_one_failure(poller):
    instance, fake = poller
    config = instance._store.get()

    await instance._poll_slot(0, config)
    fake.fail = True
    await instance._poll_slot(0, config)
    board = instance.boards()[0]

    # Still within stale_after, so the last good board shows without the flag.
    assert not board.stale
    assert len(board.services) == 4
    assert instance.last_error == "HTTP 503 for PAD"


async def test_an_old_board_is_flagged_stale(poller):
    instance, fake = poller
    config = instance._store.get()

    await instance._poll_slot(0, config)
    key = next(iter(instance._boards))
    aged = datetime.now().astimezone() - timedelta(seconds=config.sources.stale_after + 60)
    instance._boards[key] = instance._boards[key].model_copy(update={"fetched_at": aged})

    fake.fail = True
    await instance._poll_slot(0, config)
    board = instance.boards()[0]

    assert board.stale
    assert board.error == "HTTP 503 for PAD"
    assert len(board.services) == 4  # last good data is still on screen


async def test_backoff_grows_with_consecutive_failures(poller):
    instance, fake = poller
    config = instance._store.get()
    fake.fail = True
    key = "0:PAD:departures"

    delays = []
    for _ in range(3):
        before = datetime.now()
        await instance._poll_slot(0, config)
        delays.append((instance._next_due[key] - before).total_seconds())

    assert delays[0] >= config.sources.poll_interval
    assert delays[1] > delays[0]
    assert delays[2] > delays[1]


async def test_success_resets_the_backoff(poller):
    instance, fake = poller
    config = instance._store.get()
    fake.fail = True
    await instance._poll_slot(0, config)

    fake.fail = False
    before = datetime.now()
    await instance._poll_slot(0, config)
    delay = (instance._next_due["0:PAD:departures"] - before).total_seconds()

    assert delay <= config.sources.poll_interval + 1
    assert instance.last_error is None


async def test_name_override_is_applied(poller, tmp_path):
    instance, _ = poller
    instance._store.set(
        Config(stations=[{"crs": "PAD", "name": "Paddington"}], sources={"fallback": None}),
        persist=False,
    )

    await instance._poll_slot(0, instance._store.get())

    assert instance.boards()[0].name == "Paddington"


async def test_state_snapshot_shape(poller):
    instance, _ = poller
    await instance._poll_slot(0, instance._store.get())
    state = instance.state()

    assert state["type"] == "state"
    assert state["display"]["theme"] == "modern"
    assert state["stations"][0]["crs"] == "PAD"
    assert state["boards"][0]["services"][0]["destination"] == "Bristol Temple Meads"
    assert state["display_on"] is True


async def test_subscribers_receive_published_state(poller):
    instance, _ = poller
    queue = instance.subscribe()

    instance._publish(instance.state())

    assert (await queue.get())["type"] == "state"
    instance.unsubscribe(queue)
    assert queue not in instance._subscribers


async def test_a_full_subscriber_queue_drops_the_oldest_frame(poller):
    instance, _ = poller
    queue = instance.subscribe()

    for _ in range(queue.maxsize + 2):
        instance._publish(instance.state())

    assert queue.qsize() == queue.maxsize  # the poller never blocks on a slow client
