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
def arrdep_payload() -> dict:
    """The combined product's board: PAD departures and arrivals in one."""
    return load_fixture("pad_arrdep.json")


@pytest.fixture
def lbg_arrdep_payload() -> dict:
    """One real RDM capture at LBG, with formation and previous-point actuals."""
    return load_fixture("lbg_arrdep_formation.json")


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


@pytest.fixture(autouse=True)
def no_update_checks(monkeypatch) -> None:
    """The app's first update check is a git fetch from GitHub; tests never get there."""
    from describer import updater

    monkeypatch.setattr(updater, "STARTUP_DELAY", 10**9)


@pytest.fixture(autouse=True)
def no_weather_network(monkeypatch) -> None:
    """WeatherService's own loop must never reach api.open-meteo.com in a test.

    show_weather defaults off, which already stops it, but an app test that
    turns the option on (or one exercising WeatherService directly) must still
    never make a real request — Addendum 4's rule applies to every upstream,
    not only RTT. Only the *default* transport is poisoned, so test_weather.py's
    own unit tests, which hand WeatherClient an explicit httpx.MockTransport,
    are unaffected.
    """
    import httpx

    from describer import weather

    class _NoNetwork(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("network disabled in tests", request=request)

    real_init = weather.WeatherClient.__init__

    def guarded_init(self, base_url, *, timeout=10.0, transport=None):
        real_init(self, base_url, timeout=timeout, transport=transport or _NoNetwork())

    monkeypatch.setattr(weather.WeatherClient, "__init__", guarded_init)


@pytest.fixture
def rtt_credentials(monkeypatch, clean_credentials) -> None:
    """A source with no credentials is treated as permanently down."""
    monkeypatch.setenv("RTT_TOKEN", "test-token")


@pytest.fixture
def rdm_credentials(monkeypatch, clean_credentials) -> None:
    monkeypatch.setenv("RDM_API_KEY", "test-key")
