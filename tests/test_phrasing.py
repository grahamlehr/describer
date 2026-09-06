"""Announcement wording."""

import pytest

from describer.announce.phrasing import (
    AnnouncementKind,
    build_text,
    cancelled_text,
    delayed_text,
    join_places,
    speak_time,
    spell_number,
)
from describer.rail.ldbws import parse_board


@pytest.fixture
def services(departures_payload):
    return parse_board(departures_payload, "PAD", "departures").services


def test_spell_number():
    assert spell_number(0) == "zero"
    assert spell_number(9) == "nine"
    assert spell_number(15) == "fifteen"
    assert spell_number(30) == "thirty"
    assert spell_number(42) == "forty two"


def test_speak_time():
    assert speak_time("14:32") == "fourteen thirty two"
    assert speak_time("14:00") == "fourteen hundred hours"
    assert speak_time("09:05") == "nine oh five"
    assert speak_time(None) == ""


def test_join_places():
    assert join_places(["Reading"]) == "Reading"
    assert join_places(["Reading", "Slough"]) == "Reading and Slough"
    assert join_places(["A", "B", "C"]) == "A, B and C"


def test_arriving_announcement_matches_station_phrasing(services):
    text = build_text(AnnouncementKind.ARRIVING, services[0])

    assert text == (
        "The next train to arrive at platform nine will be the fourteen thirty two "
        "Great Western Railway service to Bristol Temple Meads, calling at Reading, "
        "Swindon, Bristol Parkway and Bristol Temple Meads."
    )


def test_arriving_announcement_notes_lateness(services):
    text = build_text(AnnouncementKind.ARRIVING, services[1])

    assert "platform twelve" in text
    assert text.endswith("This train is running approximately fifteen minutes late.")


def test_arriving_without_platform(services):
    service = services[0].model_copy(update={"platform": None})

    assert build_text(AnnouncementKind.ARRIVING, service).startswith(
        "The next train to arrive at this station will be"
    )


def test_calling_points_are_trimmed_but_keep_the_destination(services):
    text = build_text(AnnouncementKind.ARRIVING, services[0], max_calling_points=2)

    assert "calling at Reading and Bristol Temple Meads." in text


def test_delay_announcement(services):
    text = delayed_text(services[1])

    assert text == (
        "We are sorry to announce that the fourteen thirty six Elizabeth line "
        "service to Abbey Wood via Whitechapel is delayed by approximately "
        "fifteen minutes. This is due to a fault with the signalling system. "
        "We apologise for the delay to your journey."
    )


def test_cancellation_announcement(services):
    text = cancelled_text(services[2])

    assert text == (
        "We are sorry to announce that the fourteen forty one Great Western Railway "
        "service to Oxford has been cancelled. This is due to a shortage of train "
        "crew. We apologise for the inconvenience this will cause to your journey."
    )


def test_arrivals_mode_says_from(arrivals_payload):
    service = parse_board(arrivals_payload, "RDG", "arrivals").services[0]
    text = build_text(AnnouncementKind.ARRIVING, service, "arrivals")

    assert "service from London Paddington." in text
    assert "calling at" not in text
