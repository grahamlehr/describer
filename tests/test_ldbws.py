"""Parsing recorded LDBWS responses into our own models."""

from describer.rail.ldbws import _delay_minutes, _strip_html, parse_board
from describer.rail.models import ServiceStatus


def test_parses_departures(departures_payload):
    board = parse_board(departures_payload, "PAD", "departures")

    assert board.crs == "PAD"
    assert board.name == "London Paddington"
    assert board.mode == "departures"
    assert board.generated_at is not None
    assert len(board.services) == 4


def test_strips_html_from_messages(departures_payload):
    board = parse_board(departures_payload, "PAD", "departures")

    assert board.messages == ["Services running to and from this station are subject to delays ."]


def test_on_time_service(departures_payload):
    service = parse_board(departures_payload, "PAD", "departures").services[0]

    assert service.scheduled_time == "14:32"
    assert service.destination == "Bristol Temple Meads"
    assert service.platform == "9"
    assert service.operator_code == "GW"
    assert service.status is ServiceStatus.ON_TIME
    assert service.delay_minutes == 0
    assert service.status_text == "On time"
    assert [point.name for point in service.calling_points] == [
        "Reading",
        "Swindon",
        "Bristol Parkway",
        "Bristol Temple Meads",
    ]


def test_delayed_service_keeps_via_and_delay(departures_payload):
    service = parse_board(departures_payload, "PAD", "departures").services[1]

    assert service.destination == "Abbey Wood via Whitechapel"
    assert service.status is ServiceStatus.EXPECTED
    assert service.delay_minutes == 15
    assert service.status_text == "Exp 14:51"
    assert service.effective_time() == "14:51"


def test_cancelled_service(departures_payload):
    service = parse_board(departures_payload, "PAD", "departures").services[2]

    assert service.status is ServiceStatus.CANCELLED
    assert service.is_cancelled
    assert service.delay_minutes == 0
    assert service.cancel_reason == "This is due to a shortage of train crew"


def test_delayed_without_estimate(departures_payload):
    service = parse_board(departures_payload, "PAD", "departures").services[3]

    assert service.status is ServiceStatus.DELAYED
    assert service.status_text == "Delayed"


def test_parses_arrivals(arrivals_payload):
    board = parse_board(arrivals_payload, "RDG", "arrivals")
    first, second = board.services

    assert board.mode == "arrivals"
    assert board.messages == []
    assert first.origin == "London Paddington"
    assert first.scheduled_time == "14:56"
    assert first.status is ServiceStatus.ON_TIME
    assert [point.name for point in first.calling_points] == ["London Paddington"]
    assert second.status is ServiceStatus.EXPECTED
    assert second.delay_minutes == 7


def test_empty_payload_yields_empty_board():
    board = parse_board({}, "PAD", "departures")

    assert board.services == []
    assert board.name == "PAD"


def test_delay_minutes_wraps_midnight():
    assert _delay_minutes("23:55", "00:07") == 12
    assert _delay_minutes("14:32", "14:32") == 0
    assert _delay_minutes("14:32", "On time") == 0
    assert _delay_minutes(None, "14:40") == 0


def test_strip_html_collapses_whitespace():
    assert _strip_html("<p>Hello   <b>there</b></p>") == "Hello there"
