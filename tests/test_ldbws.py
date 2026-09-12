"""Parsing recorded LDBWS responses into our own models."""

from describer.rail.ldbws import (
    _calling_points,
    _delay_minutes,
    _formation,
    _position,
    _strip_html,
    parse_board,
)
from describer.rail.models import ServiceStatus


def _service(payload: dict, service_id: str) -> dict:
    return next(s for s in payload["trainServices"] if s["serviceID"] == service_id)


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


def test_combined_board_departures_drop_terminating_services(arrdep_payload):
    board = parse_board(arrdep_payload, "PAD", "departures")

    assert len(board.services) == 4
    assert all(service.scheduled_time for service in board.services)
    assert "Oxford" not in [service.origin for service in board.services]


def test_combined_board_arrivals_drop_originating_services(arrdep_payload):
    board = parse_board(arrdep_payload, "PAD", "arrivals")

    assert [service.scheduled_time for service in board.services] == ["14:38", "14:44"]
    assert [service.origin for service in board.services] == ["Oxford", "Abbey Wood"]


def test_combined_board_arrivals_read_previous_calling_points(arrdep_payload):
    service = parse_board(arrdep_payload, "PAD", "arrivals").services[0]

    assert service.status is ServiceStatus.ON_TIME
    assert [point.name for point in service.calling_points] == ["Oxford", "Reading", "Slough"]


def test_combined_board_arrivals_keep_delay(arrdep_payload):
    service = parse_board(arrdep_payload, "PAD", "arrivals").services[1]

    assert service.status is ServiceStatus.EXPECTED
    assert service.delay_minutes == 15
    assert service.status_text == "Exp 14:59"


# -- position, from the real LBG capture --------------------------------------


def test_position_between_reported_and_unreported_stops(lbg_arrdep_payload):
    raw = _service(lbg_arrdep_payload, "8648567LNDNBDC_")

    position = _position(raw)

    assert position.state == "between"
    assert position.last == "Oxted"
    assert position.last_time == "19:54"
    assert position.next == "East Croydon"
    assert position.stops_away == 1


def test_position_approaching_when_every_stop_is_reported(lbg_arrdep_payload):
    raw = _service(lbg_arrdep_payload, "8661442LNDNBDE_")

    position = _position(raw)

    assert position.state == "approaching"
    assert position.last == "Hither Green"
    # "On time" in `at` becomes the point's own `st`.
    assert position.last_time == "19:45"
    assert position.next is None
    assert position.stops_away == 0


def test_position_none_when_service_originates_here(lbg_arrdep_payload):
    raw = _service(lbg_arrdep_payload, "8651283LNDNBDC_")

    assert "previousCallingPoints" not in raw
    assert _position(raw) is None


def test_position_none_when_service_cancelled():
    raw = {
        "isCancelled": True,
        "previousCallingPoints": [
            {"callingPoint": [{"locationName": "Oxted", "st": "19:20", "at": "19:54"}]}
        ],
    }

    assert _position(raw) is None


def test_position_not_started_when_nothing_reported():
    raw = {
        "origin": [{"locationName": "Brighton", "crs": "BTN"}],
        "previousCallingPoints": [
            {
                "callingPoint": [
                    {"locationName": "Preston Park", "st": "19:05", "at": "No report"},
                    {"locationName": "Haywards Heath", "st": "19:12", "at": "No report"},
                ]
            }
        ],
    }

    position = _position(raw)

    assert position.state == "not_started"
    assert position.next == "Brighton"
    assert position.stops_away == 2


def test_position_skips_cancelled_calling_points():
    raw = {
        "previousCallingPoints": [
            {
                "callingPoint": [
                    {"locationName": "A", "st": "19:00", "at": "19:00", "isCancelled": False},
                    {"locationName": "B", "st": "19:05", "at": "No report", "isCancelled": True},
                    {"locationName": "C", "st": "19:10", "at": "No report", "isCancelled": False},
                ]
            }
        ],
    }

    position = _position(raw)

    assert position.state == "between"
    assert position.last == "A"
    assert position.next == "C"
    assert position.stops_away == 1


def test_calling_points_carry_actual_time(lbg_arrdep_payload):
    raw = _service(lbg_arrdep_payload, "8648567LNDNBDC_")

    points = _calling_points(raw, "arrivals")
    oxted = next(point for point in points if point.name == "Oxted")

    assert oxted.actual_time == "19:54"


# -- formation, from the real LBG capture --------------------------------------


def test_formation_parses_real_coaches(lbg_arrdep_payload):
    raw = _service(lbg_arrdep_payload, "8661532LNDNBDE_")

    formation = _formation(raw)

    assert [coach.number for coach in formation.coaches] == [
        "A1",
        "A2",
        "A3",
        "A4",
        "B1",
        "B2",
        "B3",
        "B4",
    ]
    assert formation.coaches[0].loading == 0
    assert formation.coaches[2].accessible_toilet is True
    assert formation.coaches[0].accessible_toilet is False
    assert formation.average_loading == 24


def test_formation_loading_unspecified_gives_none(lbg_arrdep_payload):
    raw = _service(lbg_arrdep_payload, "8662760LNDNBDE_")

    formation = _formation(raw)

    assert all(coach.loading is None for coach in formation.coaches)
    assert formation.average_loading is None


def test_formation_none_when_absent(lbg_arrdep_payload):
    raw = _service(lbg_arrdep_payload, "8666929LNDNBDE_")

    assert "formation" not in raw
    assert _formation(raw) is None


def test_formation_marks_first_class():
    raw = {
        "formation": {
            "coaches": [
                {"number": "A", "coachClass": "First", "loadingSpecified": False},
                {"number": "B", "coachClass": "Standard", "loadingSpecified": False},
            ]
        }
    }

    formation = _formation(raw)

    assert formation.coaches[0].first_class is True
    assert formation.coaches[1].first_class is False


def test_formation_reverses_when_isReverseFormation():
    raw = {
        "isReverseFormation": True,
        "formation": {
            "coaches": [
                {"number": "A1", "loadingSpecified": False},
                {"number": "B1", "loadingSpecified": False},
            ]
        },
    }

    formation = _formation(raw)

    assert [coach.number for coach in formation.coaches] == ["B1", "A1"]


def test_length_backfilled_from_coaches_when_missing():
    payload = {
        "trainServices": [
            {
                "serviceID": "ggg777",
                "std": "20:10",
                "etd": "On time",
                "origin": [{"locationName": "Somewhere"}],
                "destination": [{"locationName": "Elsewhere"}],
                "length": 0,
                "formation": {
                    "coaches": [
                        {"number": "A", "loadingSpecified": False},
                        {"number": "B", "loadingSpecified": False},
                        {"number": "C", "loadingSpecified": False},
                    ]
                },
            }
        ]
    }

    service = parse_board(payload, "LBG", "departures").services[0]

    assert service.length == 3
