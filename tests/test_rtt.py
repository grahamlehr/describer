"""Parsing Realtime Trains v2 responses, and parity with LDBWS.

The board fixtures carry the real shapes of the next-generation API — field
names and nesting are as captured from api.rtt.io — with values chosen to
describe the same four trains as the LDBWS fixtures. Where both feeds can say
something, they must agree, or a failover would visibly change the board.
``rtt_live_capture.json`` is an untrimmed capture kept to catch shape drift.
"""

import pytest

from describer.rail import ldbws
from describer.rail.base import RailApiError
from describer.rail.models import ServiceStatus
from describer.rail.rtt import (
    RttClient,
    _hhmm,
    has_credentials,
    parse_board,
    parse_calling_points,
)


def test_parses_departures(rtt_departures_payload):
    board = parse_board(rtt_departures_payload, "PAD", "departures")

    assert board.crs == "PAD"
    assert board.name == "London Paddington"
    assert board.mode == "departures"
    assert board.messages == []  # RTT carries no NRCC disruption messages
    assert len(board.services) == 4


def test_service_ids_are_namespaced(rtt_departures_payload):
    board = parse_board(rtt_departures_payload, "PAD", "departures")

    assert board.services[0].id == "rtt:gb-nr:W12345:2024-05-14"


def test_iso_times_become_board_times(rtt_departures_payload):
    service = parse_board(rtt_departures_payload, "PAD", "departures").services[0]

    assert service.scheduled_time == "14:32"
    assert _hhmm("2024-05-14T14:32:00") == "14:32"
    assert _hhmm("2024-05-14T14:32:00+01:00") == "14:32"
    assert _hhmm("") is None
    assert _hhmm(None) is None


def test_on_time_service_matches_ldbws(rtt_departures_payload, departures_payload):
    rtt = parse_board(rtt_departures_payload, "PAD", "departures").services[0]
    rdm = ldbws.parse_board(departures_payload, "PAD", "departures").services[0]

    assert (rtt.scheduled_time, rtt.status, rtt.delay_minutes) == (
        rdm.scheduled_time,
        rdm.status,
        rdm.delay_minutes,
    )
    assert rtt.destination == rdm.destination == "Bristol Temple Meads"
    assert rtt.operator == "Great Western Railway"
    assert rtt.operator_code == "GW"
    assert rtt.platform == "9"
    assert rtt.length == rdm.length == 9
    assert rtt.status_text == "On time"


def test_delayed_service_matches_ldbws(rtt_departures_payload, departures_payload):
    rtt = parse_board(rtt_departures_payload, "PAD", "departures").services[1]
    rdm = ldbws.parse_board(departures_payload, "PAD", "departures").services[1]

    assert rtt.status is rdm.status is ServiceStatus.EXPECTED
    assert rtt.delay_minutes == rdm.delay_minutes == 15
    assert rtt.status_text == rdm.status_text == "Exp 14:51"
    assert rtt.effective_time() == "14:51"
    # v2 carries reason text, which the v1 API never did.
    assert rtt.delay_reason == rdm.delay_reason


def test_cancelled_service_matches_ldbws(rtt_departures_payload, departures_payload):
    rtt = parse_board(rtt_departures_payload, "PAD", "departures").services[2]
    rdm = ldbws.parse_board(departures_payload, "PAD", "departures").services[2]

    assert rtt.status is rdm.status is ServiceStatus.CANCELLED
    assert rtt.is_cancelled
    assert rtt.delay_minutes == 0
    assert rtt.cancel_reason == rdm.cancel_reason == "This is due to a shortage of train crew"


def test_no_estimate_is_unknown_not_delayed(rtt_departures_payload):
    """RTT has no bare "Delayed" state, so we must not invent one."""
    service = parse_board(rtt_departures_payload, "PAD", "departures").services[3]

    assert service.status is ServiceStatus.UNKNOWN
    assert service.status_text == ""


def test_an_unconfirmed_platform_is_withheld(rtt_departures_payload):
    """Only a forecast or actual platform is real; planned alone is not."""
    service = parse_board(rtt_departures_payload, "PAD", "departures").services[3]

    assert service.platform is None


def test_rtts_own_lateness_figure_is_preferred(rtt_live_payload):
    """Where the API reports lateness, we use it rather than subtracting clocks."""
    board = parse_board(rtt_live_payload, "PAD", "arrivals")

    assert all(service.delay_minutes >= 0 for service in board.services)


def test_buses_and_non_passenger_services_are_skipped(rtt_departures_payload):
    board = parse_board(rtt_departures_payload, "PAD", "departures")

    assert [s.destination for s in board.services] == [
        "Bristol Temple Meads",
        "Abbey Wood",
        "Oxford",
        "Heathrow Terminal 5",
    ]


def test_buses_can_be_opted_in(rtt_departures_payload):
    board = parse_board(rtt_departures_payload, "PAD", "departures", include_buses=True)

    # The bus joins the board; the empty-stock working still does not.
    assert "Reading" in [s.destination for s in board.services]
    assert len(board.services) == 5


def test_a_set_down_call_is_not_a_departure(rtt_departures_payload):
    """A train nobody may board here does not belong on a departure board."""
    departures = parse_board(rtt_departures_payload, "PAD", "departures")
    arrivals = parse_board(rtt_departures_payload, "PAD", "arrivals")

    assert "W33333" not in " ".join(s.id for s in departures.services)
    assert "W33333" in " ".join(s.id for s in arrivals.services)


def test_parses_arrivals(rtt_arrivals_payload, arrivals_payload):
    board = parse_board(rtt_arrivals_payload, "RDG", "arrivals")
    rdm = ldbws.parse_board(arrivals_payload, "RDG", "arrivals")
    first, second = board.services

    assert board.mode == "arrivals"
    assert board.name == "Reading"
    assert first.origin == rdm.services[0].origin == "London Paddington"
    assert first.scheduled_time == rdm.services[0].scheduled_time == "14:56"
    assert first.status is rdm.services[0].status is ServiceStatus.ON_TIME
    assert second.status is rdm.services[1].status is ServiceStatus.EXPECTED
    assert second.delay_minutes == rdm.services[1].delay_minutes == 7


def test_services_are_ordered_by_time(rtt_departures_payload):
    board = parse_board(rtt_departures_payload, "PAD", "departures")

    times = [service.scheduled_time for service in board.services]
    assert times == sorted(times)


def test_calling_points_after_this_station(rtt_detail_payload, departures_payload):
    points = parse_calling_points(rtt_detail_payload, "PAD", "departures")
    rdm = ldbws.parse_board(departures_payload, "PAD", "departures").services[0]

    assert [p.name for p in points] == [p.name for p in rdm.calling_points]
    assert [p.scheduled_time for p in points] == [p.scheduled_time for p in rdm.calling_points]
    assert points[0].crs == "RDG"


def test_passing_points_are_not_calling_points(rtt_detail_payload):
    points = parse_calling_points(rtt_detail_payload, "PAD", "departures")

    assert "Didcot Parkway" not in [p.name for p in points]


def test_calling_points_before_this_station(rtt_detail_payload, arrivals_payload):
    points = parse_calling_points(rtt_detail_payload, "RDG", "arrivals")
    rdm = ldbws.parse_board(arrivals_payload, "RDG", "arrivals").services[0]

    assert [p.name for p in points] == [p.name for p in rdm.calling_points]
    # A previous calling point is timed by when the train left it.
    assert [p.scheduled_time for p in points] == [p.scheduled_time for p in rdm.calling_points]


def test_calling_points_of_an_unknown_station_are_empty(rtt_detail_payload):
    assert parse_calling_points(rtt_detail_payload, "OXF", "departures") == []


def test_empty_payload_yields_empty_board():
    board = parse_board({}, "PAD", "departures")

    assert board.services == []
    assert board.name == "PAD"


def test_a_real_capture_still_parses(rtt_live_payload):
    """Guards against the API changing shape under us."""
    departures = parse_board(rtt_live_payload, "PAD", "departures")
    arrivals = parse_board(rtt_live_payload, "PAD", "arrivals")

    assert departures.name == "London Paddington"
    assert departures.crs == "PAD"
    assert departures.services or arrivals.services
    for service in departures.services + arrivals.services:
        assert service.scheduled_time and ":" in service.scheduled_time
        assert service.operator and service.operator_code
        assert service.destination or service.origin


def test_credentials_come_from_one_token(monkeypatch):
    monkeypatch.delenv("RTT_TOKEN", raising=False)
    assert not has_credentials()
    monkeypatch.setenv("RTT_TOKEN", "abc")
    assert has_credentials()


@pytest.fixture
def recording_client(rtt_credentials, rtt_departures_payload, rtt_detail_payload):
    """An RttClient whose HTTP layer is replaced by the recorded fixtures."""
    client = RttClient("https://data.rtt.io", detail_rows=2)
    requests: list[tuple[str, dict, str]] = []

    async def fake_request(path, *, bearer, what, params=None):
        requests.append((path, params or {}, bearer))
        if path == "/api/get_access_token":
            return {"token": "access-token", "validUntil": "2099-01-01T00:00:00+00:00"}
        if path == "/rtt/service":
            return rtt_detail_payload
        return rtt_departures_payload

    client._request = fake_request
    return client, requests


async def test_the_refresh_token_is_exchanged_once(recording_client):
    client, requests = recording_client

    await client.fetch_board("PAD", "departures")
    await client.fetch_board("PAD", "departures")

    exchanges = [r for r in requests if r[0] == "/api/get_access_token"]
    assert len(exchanges) == 1
    assert exchanges[0][2] == "test-token"  # the configured token buys the access one
    # Every data call then carries the short-life token, never the refresh one.
    assert all(r[2] == "access-token" for r in requests if r[0] != "/api/get_access_token")
    await client.aclose()


async def test_a_long_life_access_token_is_used_directly(rtt_credentials):
    """A token the exchange refuses is an access token; use it as it is."""
    client = RttClient("https://data.rtt.io")
    seen: list[tuple[str, str]] = []

    async def fake_request(path, *, bearer, what, params=None):
        seen.append((path, bearer))
        if path == "/api/get_access_token":
            return None  # 401: this is not a refresh token
        return {"query": {"location": {"description": "X", "shortCodes": ["PAD"]}}}

    client._request = fake_request
    await client.fetch_board("PAD", "departures")
    await client.fetch_board("PAD", "departures")

    assert [p for p, _ in seen].count("/api/get_access_token") == 1
    assert all(bearer == "test-token" for path, bearer in seen if path == "/rtt/location")
    await client.aclose()


async def test_a_board_asks_for_details_only_where_they_show(recording_client):
    client, requests = recording_client

    board = await client.fetch_board("PAD", "departures")

    calls = [(path, params) for path, params, _ in requests]
    assert calls[1][0] == "/rtt/location"
    assert calls[1][1]["code"] == "gb-nr:PAD"
    # detail_rows=2, so only the first two rows cost a round trip.
    details = [params["uniqueIdentity"] for path, params in calls if path == "/rtt/service"]
    assert details == ["gb-nr:W12345:2024-05-14", "gb-nr:W67890:2024-05-14"]
    assert [p.name for p in board.services[0].calling_points][0] == "Reading"
    assert board.services[2].calling_points == []
    await client.aclose()


async def test_details_are_cached_between_polls(recording_client):
    client, requests = recording_client

    await client.fetch_board("PAD", "departures")
    await client.fetch_board("PAD", "departures")

    paths = [path for path, _, _ in requests]
    assert paths.count("/rtt/location") == 2
    assert paths.count("/rtt/service") == 2  # two rows, fetched once each
    await client.aclose()


async def test_an_unusable_token_is_an_error_not_a_crash(rtt_credentials):
    client = RttClient("https://data.rtt.io")

    async def always_unauthorised(path, *, bearer, what, params=None):
        return None

    client._request = always_unauthorised
    with pytest.raises(RailApiError, match="Not authorised"):
        await client.fetch_board("PAD", "departures")
    await client.aclose()


async def test_a_missing_token_is_reported_before_any_request(monkeypatch):
    monkeypatch.delenv("RTT_TOKEN", raising=False)
    client = RttClient("https://data.rtt.io")

    with pytest.raises(RailApiError, match="RTT_TOKEN"):
        await client.fetch_board("PAD", "departures")
    await client.aclose()


@pytest.mark.parametrize("allowance", [{"minute": 1}, {"minute": 9, "hour": 20}])
async def test_calling_points_are_dropped_before_the_board_is(recording_client, allowance):
    """With either allowance nearly gone, the board still arrives."""
    client, requests = recording_client
    client.rate_limit = allowance

    board = await client.fetch_board("PAD", "departures")

    assert [path for path, _, _ in requests].count("/rtt/service") == 0
    assert len(board.services) == 4  # the board itself is never sacrificed
    assert board.services[0].calling_points == []
    await client.aclose()


async def test_the_remaining_allowance_is_recorded(rtt_credentials):
    client = RttClient("https://data.rtt.io")

    client._note_rate_limit(
        {
            "X-RateLimit-Remaining-Minute": "8",
            "X-RateLimit-Remaining-Hour": "94",
            "X-RateLimit-Remaining-Day": "980",
        }
    )

    assert client.rate_limit == {"minute": 8, "hour": 94, "day": 980}
    await client.aclose()


async def test_a_healthy_allowance_still_buys_calling_points(recording_client):
    client, requests = recording_client
    client.rate_limit = {"minute": 8, "hour": 90}

    await client.fetch_board("PAD", "departures")

    assert [path for path, _, _ in requests].count("/rtt/service") == 2
    await client.aclose()


def test_the_poll_floor_defaults_to_the_free_tier():
    from describer.config import RttSourceConfig

    assert RttSourceConfig().min_poll_interval == 120
    assert RttSourceConfig().detail_rows == 3
