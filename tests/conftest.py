import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def departures_payload() -> dict:
    return load_fixture("pad_departures.json")


@pytest.fixture
def arrivals_payload() -> dict:
    return load_fixture("rdg_arrivals.json")


@pytest.fixture
def rtt_departures_payload() -> dict:
    return load_fixture("rtt_pad_departures.json")


@pytest.fixture
def rtt_arrivals_payload() -> dict:
    return load_fixture("rtt_rdg_arrivals.json")


@pytest.fixture
def rtt_detail_payload() -> dict:
    return load_fixture("rtt_service_detail.json")


@pytest.fixture
def rtt_live_payload() -> dict:
    """An untouched capture from the real API, to catch shape drift."""
    return load_fixture("rtt_live_capture.json")


@pytest.fixture(autouse=True)
def clean_credentials(monkeypatch) -> None:
    """No test inherits the developer's real keys; each opts in below."""
    for name in ("RDM_API_KEY", "RTT_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def rtt_credentials(monkeypatch, clean_credentials) -> None:
    """A source with no credentials is treated as permanently down."""
    monkeypatch.setenv("RTT_TOKEN", "test-token")


@pytest.fixture
def rdm_credentials(monkeypatch, clean_credentials) -> None:
    monkeypatch.setenv("RDM_API_KEY", "test-key")
