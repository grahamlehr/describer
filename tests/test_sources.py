"""Choosing a source, failing over to the other one, and coming back."""

import contextlib
import logging
import time

import pytest

from describer.config import SourcesConfig
from describer.rail import sources as sources_module
from describer.rail.base import RailApiError
from describer.rail.models import Board
from describer.rail.sources import SourceManager


class FakeSource:
    """Stands in for a real client; can be told to fail."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fail = False
        self.calls = 0
        self.closed = False
        self.min_poll_interval = 0
        self.rate_limit: dict[str, int] = {}

    async def fetch_board(self, crs: str, mode: str = "departures") -> Board:
        self.calls += 1
        if self.fail:
            raise RailApiError(f"HTTP 503 for {crs}")
        return Board(crs=crs.upper(), name=f"{crs} via {self.name}", mode=mode)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def manager(monkeypatch, rdm_credentials, rtt_credentials):
    """A manager over two fake clients, both authenticated."""
    rdm, rtt = FakeSource("rdm"), FakeSource("rtt")
    monkeypatch.setattr(sources_module, "LdbwsClient", lambda *a, **k: rdm)
    monkeypatch.setattr(sources_module, "RttClient", lambda *a, **k: rtt)
    return SourceManager(SourcesConfig(failover_after=3)), rdm, rtt


async def trip_the_failover(instance, calls: int = 3) -> None:
    """Fail the primary often enough that the fallback takes over."""
    for _ in range(calls):
        with contextlib.suppress(RailApiError):
            await instance.fetch_board("PAD", "departures")


async def test_the_primary_answers_while_it_works(manager):
    instance, rdm, rtt = manager

    board = await instance.fetch_board("PAD", "departures")

    assert instance.active == "rdm"
    assert (rdm.calls, rtt.calls) == (1, 0)
    assert board.source == "rdm"


async def test_failover_only_after_enough_failures(manager):
    instance, rdm, _ = manager
    rdm.fail = True

    for _ in range(2):
        with pytest.raises(RailApiError):
            await instance.fetch_board("PAD", "departures")
    assert instance.active == "rdm"  # two failures is not yet a pattern

    board = await instance.fetch_board("PAD", "departures")

    assert instance.active == "rtt"
    assert board.source == "rtt"
    assert board.name == "PAD via rtt"


async def test_failures_are_counted_across_stations(manager):
    """Both halves of a split screen feed the same counter."""
    instance, rdm, _ = manager
    rdm.fail = True

    with pytest.raises(RailApiError):
        await instance.fetch_board("PAD", "departures")
    with pytest.raises(RailApiError):
        await instance.fetch_board("RDG", "arrivals")
    await instance.fetch_board("PAD", "departures")

    assert instance.active == "rtt"


async def test_both_halves_agree_after_a_failover(manager):
    instance, rdm, _ = manager
    rdm.fail = True
    for _ in range(2):
        with pytest.raises(RailApiError):
            await instance.fetch_board("PAD", "departures")

    first = await instance.fetch_board("PAD", "departures")
    second = await instance.fetch_board("RDG", "arrivals")

    assert first.source == second.source == "rtt"


async def test_the_primary_is_retried_and_taken_back(manager, caplog):
    instance, rdm, rtt = manager
    rdm.fail = True
    await trip_the_failover(instance)
    assert instance.active == "rtt"

    # Still inside recover_after: the primary is left alone.
    calls_before = rdm.calls
    await instance.fetch_board("PAD", "departures")
    assert rdm.calls == calls_before

    rdm.fail = False
    instance._next_probe = time.monotonic() - 1
    with caplog.at_level(logging.INFO):
        board = await instance.fetch_board("PAD", "departures")

    assert instance.active == "rdm"
    assert board.source == "rdm"
    assert any("recovered" in record.message for record in caplog.records)


async def test_a_failed_probe_leaves_the_fallback_in_place(manager):
    instance, rdm, rtt = manager
    rdm.fail = True
    await trip_the_failover(instance)

    instance._next_probe = time.monotonic() - 1
    board = await instance.fetch_board("PAD", "departures")

    assert instance.active == "rtt"
    assert board.source == "rtt"


async def test_a_failing_fallback_still_raises(manager):
    """Failover must not paper over a board that has no data at all."""
    instance, rdm, rtt = manager
    rdm.fail = rtt.fail = True

    for _ in range(4):
        with pytest.raises(RailApiError):
            await instance.fetch_board("PAD", "departures")

    assert instance.active == "rtt"


async def test_no_fallback_means_no_failover(monkeypatch, rdm_credentials, rtt_credentials):
    rdm, rtt = FakeSource("rdm"), FakeSource("rtt")
    monkeypatch.setattr(sources_module, "LdbwsClient", lambda *a, **k: rdm)
    monkeypatch.setattr(sources_module, "RttClient", lambda *a, **k: rtt)
    instance = SourceManager(SourcesConfig(fallback=None, failover_after=1))
    rdm.fail = True

    for _ in range(3):
        with pytest.raises(RailApiError):
            await instance.fetch_board("PAD", "departures")

    assert instance.active == "rdm"
    assert rtt.calls == 0


async def test_missing_credentials_are_reported_once(monkeypatch, rdm_credentials, caplog):
    """RTT has no login here, so it is down for good — not once per tick."""
    rdm, rtt = FakeSource("rdm"), FakeSource("rtt")
    monkeypatch.setattr(sources_module, "LdbwsClient", lambda *a, **k: rdm)
    monkeypatch.setattr(sources_module, "RttClient", lambda *a, **k: rtt)

    with caplog.at_level(logging.ERROR):
        instance = SourceManager(SourcesConfig(failover_after=1))
        rdm.fail = True
        for _ in range(3):
            with pytest.raises(RailApiError):
                await instance.fetch_board("PAD", "departures")

    reports = [r.message for r in caplog.records if "no credentials" in r.message]
    assert reports == ["Source rtt has no credentials in the environment"]
    assert instance.active == "rdm"  # nowhere to fail over to
    assert rtt.calls == 0
    assert instance.status()["credentials"] == {"rdm": True, "rtt": False}


async def test_a_primary_without_credentials_starts_on_the_fallback(
    monkeypatch, rtt_credentials, caplog
):
    rdm, rtt = FakeSource("rdm"), FakeSource("rtt")
    monkeypatch.setattr(sources_module, "LdbwsClient", lambda *a, **k: rdm)
    monkeypatch.setattr(sources_module, "RttClient", lambda *a, **k: rtt)

    with caplog.at_level(logging.WARNING):
        instance = SourceManager(SourcesConfig())
    board = await instance.fetch_board("PAD", "departures")

    assert instance.active == "rtt"
    assert board.source == "rtt"
    assert rdm.calls == 0


async def test_forcing_a_source_bypasses_failover(manager):
    instance, rdm, rtt = manager

    instance.force("rtt")
    board = await instance.fetch_board("PAD", "departures")

    assert instance.active == "rtt"
    assert board.source == "rtt"
    assert rdm.calls == 0

    instance.force(None)
    assert instance.active == "rdm"
    assert (await instance.fetch_board("PAD", "departures")).source == "rdm"


async def test_forcing_an_unknown_source_is_refused(manager):
    instance, _, _ = manager

    with pytest.raises(ValueError):
        instance.force("nre")


async def test_status_describes_both_sources(manager):
    instance, rdm, _ = manager
    rdm.fail = True
    with pytest.raises(RailApiError):
        await instance.fetch_board("PAD", "departures")

    status = instance.status()

    assert status["active_source"] == "rdm"
    assert status["forced_source"] == "auto"
    assert status["primary"] == "rdm"
    assert status["fallback"] == "rtt"
    assert status["primary_healthy"] is False
    assert status["fallback_healthy"] is True
    assert status["credentials"] == {"rdm": True, "rtt": True}


async def test_changing_the_roles_resets_the_failover_state(manager):
    instance, rdm, _ = manager
    rdm.fail = True
    await trip_the_failover(instance)
    assert instance.active == "rtt"

    instance.update_config(SourcesConfig(primary="rtt", fallback="rdm"))

    assert instance.active == "rtt"  # now the primary in its own right
    assert instance._failures == 0


async def test_closing_the_manager_closes_its_clients(manager):
    instance, rdm, rtt = manager
    await instance.fetch_board("PAD", "departures")
    instance.force("rtt")
    await instance.fetch_board("PAD", "departures")

    await instance.aclose()

    assert rdm.closed and rtt.closed


def test_a_source_cannot_be_its_own_fallback():
    with pytest.raises(ValueError):
        SourcesConfig(primary="rtt", fallback="rtt")


async def test_a_metered_source_slows_the_poller(manager):
    """RTT's free tier is 100 calls an hour; a 30 s board would eat it."""
    instance, rdm, rtt = manager
    rdm.min_poll_interval = 0
    rtt.min_poll_interval = 120

    assert instance.poll_interval(30) == 30  # RDM is live and does not care

    instance.force("rtt")

    assert instance.poll_interval(30) == 120
    assert instance.poll_interval(300) == 300  # a slower board stays slower


async def test_status_reports_the_remaining_allowance(manager):
    instance, _, rtt = manager
    rtt.rate_limit = {"minute": 7, "hour": 61}
    instance.force("rtt")

    # Nothing is known until the source has actually answered once.
    assert instance.status()["rate_limit"] == {}

    await instance.fetch_board("PAD", "departures")

    assert instance.status()["rate_limit"] == {"minute": 7, "hour": 61}
