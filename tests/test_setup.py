"""First-run setup: when it is needed, the key test, finishing, and the QR code.

Everything here is offline. The key test goes through ``httpx.MockTransport``
and the app's own polling through a fake client, so no test can reach
raildata.org.uk, and none ever reaches data.rtt.io.
"""

import contextlib
import logging
import os
import stat
from types import SimpleNamespace

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from describer import main as main_module
from describer import setup
from describer.config import Config, SourcesConfig, save_config
from describer.rail import sources as sources_module
from describer.rail.base import RailApiError
from describer.rail.ldbws import LdbwsClient, parse_board
from describer.rail.models import Board
from describer.rail.sources import SourceManager

KEY = "s3cret-Key-value-1234"

LAN_IP = "192.168.1.23"

#: The real ones, kept before the autouse fixture below replaces them.
REAL_LAN_IP = setup.lan_ip
REAL_HOSTNAME = setup.hostname


@pytest.fixture(autouse=True)
def this_host(monkeypatch):
    """A fixed hostname and LAN address, so the URLs are the same everywhere."""
    monkeypatch.setattr(setup, "lan_ip", lambda: LAN_IP)
    monkeypatch.setattr(setup, "hostname", lambda: "describer")


# -- setup_state -----------------------------------------------------------


class FakeSource:
    def __init__(self, error: RailApiError | None = None) -> None:
        self.error = error
        self.calls = 0

    async def fetch_board(self, crs: str, mode: str = "departures") -> Board:
        self.calls += 1
        if self.error:
            raise self.error
        return Board(crs=crs, name=crs, mode=mode)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def world(monkeypatch):
    """A source manager over a scripted RDM, and stand-ins for poller and store."""
    rdm, rtt = FakeSource(), FakeSource()
    monkeypatch.setattr(sources_module, "LdbwsClient", lambda *a, **k: rdm)
    monkeypatch.setattr(sources_module, "RttClient", lambda *a, **k: rtt)

    def build(primary="rdm", fallback=None):
        config = SourcesConfig(primary=primary, fallback=fallback)
        manager = SourceManager(config)
        boards: list[Board] = []
        poller = SimpleNamespace(sources=manager, boards=lambda: boards)
        store = SimpleNamespace(get=lambda: SimpleNamespace(sources=config))
        return SimpleNamespace(
            manager=manager, rdm=rdm, rtt=rtt, poller=poller, store=store, boards=boards
        )

    return build


async def refused(w, status: int) -> None:
    """Have RDM answer ``status`` to one fetch."""
    w.rdm.error = RailApiError(f"HTTP {status} for PAD", status=status)
    with contextlib.suppress(RailApiError):
        await w.manager.fetch_board("PAD")


def test_no_key_needs_setup(world):
    w = world()

    assert setup.setup_state(w.poller, w.store) == {
        "reason": "no_key",
        "url": "http://describer.local:8080/setup",
        "ip_url": f"http://{LAN_IP}:8080/setup",
    }


def test_no_lan_address_leaves_only_the_name(world, monkeypatch):
    monkeypatch.setattr(setup, "lan_ip", lambda: None)
    w = world()

    state = setup.setup_state(w.poller, w.store)

    assert state["ip_url"] is None
    assert state["url"] == "http://describer.local:8080/setup"


def test_a_key_with_a_good_board_needs_nothing(world, rdm_credentials):
    w = world()

    assert setup.setup_state(w.poller, w.store) is None


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_key_needs_setup(world, rdm_credentials, status):
    w = world()
    await refused(w, status)

    assert setup.setup_state(w.poller, w.store)["reason"] == "key_rejected"


async def test_a_refused_key_does_not_stop_polling(world, rdm_credentials):
    """Setup changes what the screen says, never whether RDM is asked."""
    w = world()
    w.rdm.error = RailApiError("HTTP 401 for PAD", status=401)

    for _ in range(4):
        with pytest.raises(RailApiError):
            await w.manager.fetch_board("PAD")

    assert w.rdm.calls == 4


@pytest.mark.parametrize("status", [500, 503, None])
async def test_an_outage_is_not_setup(world, rdm_credentials, status):
    w = world()
    await refused(w, 500)  # ends up as an outage whatever the status below
    w.rdm.error = RailApiError("outage", status=status)
    with contextlib.suppress(RailApiError):
        await w.manager.fetch_board("PAD")

    assert setup.setup_state(w.poller, w.store) is None


async def test_a_board_after_a_refusal_ends_it(world, rdm_credentials):
    w = world()
    await refused(w, 401)
    w.rdm.error = None

    await w.manager.fetch_board("PAD")

    assert setup.setup_state(w.poller, w.store) is None


async def test_an_outage_after_a_refusal_is_no_longer_a_refusal(world, rdm_credentials):
    """The screen says what RDM said last."""
    w = world()
    await refused(w, 401)
    await refused(w, 503)

    assert setup.setup_state(w.poller, w.store) is None


async def test_a_new_key_forgets_the_refusal(world, rdm_credentials):
    w = world()
    await refused(w, 403)

    w.manager.credentials_changed()

    assert setup.setup_state(w.poller, w.store) is None


async def test_only_rdm_refusals_count(world, rdm_credentials, rtt_credentials):
    """A refused RTT token is the fallback's problem, never a reason to set up."""
    w = world(primary="rdm", fallback="rtt")
    w.manager.force("rtt")
    w.rtt.error = RailApiError("HTTP 401 for PAD", status=401)
    with contextlib.suppress(RailApiError):
        await w.manager.fetch_board("PAD")

    assert w.manager.rdm_auth_failed is False
    assert setup.setup_state(w.poller, w.store) is None


def test_a_missing_rtt_token_is_not_setup(world, rdm_credentials):
    w = world(primary="rdm", fallback="rtt")

    assert setup.setup_state(w.poller, w.store) is None


def test_rtt_as_the_primary_never_needs_an_rdm_key(world, rtt_credentials):
    w = world(primary="rtt", fallback=None)

    assert setup.setup_state(w.poller, w.store) is None


async def test_a_fallback_that_is_serving_keeps_the_screen_clear(
    world, rdm_credentials, rtt_credentials
):
    w = world(primary="rdm", fallback="rtt")
    await refused(w, 401)
    w.boards.append(Board(crs="PAD", name="Paddington", source="rtt"))

    assert setup.setup_state(w.poller, w.store) is None

    w.boards[0] = w.boards[0].model_copy(update={"stale": True})
    assert setup.setup_state(w.poller, w.store)["reason"] == "key_rejected"


# -- the LAN address -------------------------------------------------------


def test_lan_ip_is_none_without_a_route(monkeypatch):
    def no_route(*_args, **_kwargs):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(setup.socket, "socket", no_route)

    assert REAL_LAN_IP() is None


def test_lan_ip_ignores_loopback(monkeypatch):
    class Loopback:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def connect(self, _address):
            pass

        def getsockname(self):
            return ("127.0.0.1", 1234)

    monkeypatch.setattr(setup.socket, "socket", lambda *a, **k: Loopback())

    assert REAL_LAN_IP() is None


def test_the_hostname_loses_its_domain(monkeypatch):
    monkeypatch.setattr(setup.socket, "gethostname", lambda: "describer.lan")

    assert REAL_HOSTNAME() == "describer"


# -- the RDM client --------------------------------------------------------


async def test_the_client_says_which_status_refused_it():
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="nope"))
    client = LdbwsClient("https://rdm.test/api", transport=transport)

    with pytest.raises(RailApiError) as raised:
        await client.fetch_board("PAD", key=KEY)
    await client.aclose()

    assert raised.value.status == 403
    assert raised.value.is_auth_failure
    assert KEY not in str(raised.value)


async def test_a_key_passed_in_is_the_one_sent(departures_payload):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["x-apikey"])
        return httpx.Response(200, json=departures_payload)

    client = LdbwsClient("https://rdm.test/api", transport=httpx.MockTransport(handler))
    await client.fetch_board("PAD", key=KEY)
    await client.aclose()

    assert seen == [KEY]  # and RDM_API_KEY is not even set


# -- the app ---------------------------------------------------------------


@pytest.fixture
def make_app(monkeypatch, tmp_path, departures_payload):
    """The app on a fake RDM, with the credential file in ``tmp_path``."""
    board = parse_board(departures_payload, "PAD", "departures")

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def fetch_board(self, crs, mode="departures"):
            return board.model_copy(deep=True)

        async def aclose(self):
            pass

    monkeypatch.setattr(sources_module, "LdbwsClient", FakeClient)
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: None)
    env_file = tmp_path / "describer.env"
    monkeypatch.setenv("DESCRIBER_ENV_FILE", str(env_file))
    # credentials.write() sets os.environ itself; have teardown undo it.
    monkeypatch.setenv("RDM_API_KEY", "placeholder")
    monkeypatch.delenv("RDM_API_KEY")
    config_file = tmp_path / "config.yaml"
    monkeypatch.setenv("DESCRIBER_CONFIG", str(config_file))

    stack = contextlib.ExitStack()

    def build(*, key: str | None = None, config: Config | None = None):
        save_config(config or Config(stations=[{"crs": "PAD"}]), config_file)
        if key:
            monkeypatch.setenv("RDM_API_KEY", key)
        return stack.enter_context(TestClient(main_module.app))

    build.env_file = env_file
    build.config_file = config_file
    yield build
    stack.close()


def saved_config(make_app) -> dict:
    return yaml.safe_load(make_app.config_file.read_text())


def test_the_state_carries_no_setup_when_a_key_is_set(make_app):
    client = make_app(key="a-key")

    assert client.get("/api/state").json()["setup"] is None


def test_the_state_carries_setup_when_there_is_no_key(make_app):
    client = make_app()

    assert client.get("/api/state").json()["setup"] == {
        "reason": "no_key",
        "url": "http://describer.local:8080/setup",
        "ip_url": f"http://{LAN_IP}:8080/setup",
    }


def test_the_setup_page_and_its_files_are_served(make_app):
    client = make_app()

    for path in ("/setup", "/static/setup.js", "/static/setup.css", "/static/stationsearch.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path
    assert "Set up" in client.get("/setup").text


# -- POST /api/setup/test-key ----------------------------------------------


@pytest.fixture
def rdm_answers(monkeypatch):
    """Script what the fake RDM says to the key test, and record what it was sent."""
    script = SimpleNamespace(respond=None, requests=[])

    def handler(request: httpx.Request) -> httpx.Response:
        script.requests.append(request)
        result = script.respond(request)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        setup,
        "make_client",
        lambda base_url, timeout: LdbwsClient(
            base_url, timeout=timeout, transport=httpx.MockTransport(handler)
        ),
    )
    return script


def test_a_good_key_reports_the_station_and_the_next_train(
    make_app, rdm_answers, departures_payload
):
    client = make_app()
    rdm_answers.respond = lambda request: httpx.Response(200, json=departures_payload)

    response = client.post("/api/setup/test-key", json={"key": f"  {KEY} ", "crs": "pad"})

    body = response.json()
    assert body["result"] == "ok"
    assert body["station"] == "London Paddington"
    assert body["crs"] == "PAD"
    assert body["next_train"]["destination"]
    assert body["next_train"]["time"]
    (request,) = rdm_answers.requests
    assert request.headers["x-apikey"] == KEY  # the key sent, trimmed
    assert request.url.path.endswith("/PAD")
    assert "RDM_API_KEY" not in os.environ  # trying a key does not install it


def test_a_board_with_no_trains_is_still_ok(make_app, rdm_answers, departures_payload):
    client = make_app()
    rdm_answers.respond = lambda request: httpx.Response(
        200, json={**departures_payload, "trainServices": []}
    )

    body = client.post("/api/setup/test-key", json={"key": KEY, "crs": "PAD"}).json()

    assert body["result"] == "ok"
    assert body["next_train"] is None


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_rejected(make_app, rdm_answers, status):
    client = make_app()
    rdm_answers.respond = lambda request: httpx.Response(status, text=f"denied {KEY}")

    response = client.post("/api/setup/test-key", json={"key": KEY, "crs": "PAD"})

    assert response.json() == {"result": "rejected", "status": status}
    assert KEY not in response.text


@pytest.mark.parametrize(
    "respond",
    [
        lambda request: httpx.Response(500),
        lambda request: httpx.Response(503),
        lambda request: httpx.ConnectError("no route", request=request),
        lambda request: httpx.ReadTimeout("slow", request=request),
    ],
    ids=["500", "503", "connect", "timeout"],
)
def test_an_outage_is_unreachable(make_app, rdm_answers, respond):
    client = make_app()
    rdm_answers.respond = respond

    response = client.post("/api/setup/test-key", json={"key": KEY, "crs": "PAD"})

    assert response.json()["result"] == "unreachable"
    assert KEY not in response.text


def test_each_click_is_one_request(make_app, rdm_answers):
    client = make_app()
    rdm_answers.respond = lambda request: httpx.Response(500)

    client.post("/api/setup/test-key", json={"key": KEY, "crs": "PAD"})

    assert len(rdm_answers.requests) == 1  # no automatic retry


@pytest.mark.parametrize("key", ["", "   ", "it's", "two\nlines"])
def test_a_key_that_cannot_be_a_key_never_reaches_rdm(make_app, rdm_answers, key):
    client = make_app()

    response = client.post("/api/setup/test-key", json={"key": key, "crs": "PAD"})

    assert response.status_code == 422
    assert rdm_answers.requests == []


def test_a_bad_station_code_never_reaches_rdm(make_app, rdm_answers):
    client = make_app()

    response = client.post("/api/setup/test-key", json={"key": KEY, "crs": "P4"})

    assert response.status_code == 422
    assert rdm_answers.requests == []


@pytest.mark.parametrize("outcome", ["ok", "rejected", "unreachable", "invalid"])
def test_the_key_never_reaches_a_log_at_any_level(
    make_app, rdm_answers, departures_payload, caplog, outcome
):
    client = make_app()
    rdm_answers.respond = {
        "ok": lambda r: httpx.Response(200, json=departures_payload),
        "rejected": lambda r: httpx.Response(401, text=KEY),
        "unreachable": lambda r: httpx.ConnectError(KEY, request=r),
        "invalid": lambda r: httpx.Response(200, json=departures_payload),
    }[outcome]
    key = KEY + "'" if outcome == "invalid" else KEY
    caplog.set_level(logging.DEBUG)

    response = client.post("/api/setup/test-key", json={"key": key, "crs": "PAD"})

    assert KEY not in response.text
    assert KEY not in caplog.text
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert KEY not in str(record.args)


# -- POST /api/setup/complete ----------------------------------------------

ANSWERS = {
    "stations": [{"crs": "kgx", "mode": "arrivals"}],
    "key": KEY,
    "announce": True,
    "audio_device": "jack",
}


def test_finishing_writes_the_key_and_the_config(make_app, caplog):
    client = make_app()
    caplog.set_level(logging.DEBUG)

    response = client.post("/api/setup/complete", json=ANSWERS)

    assert response.status_code == 200, response.text
    assert response.json()["setup"] is None
    env = make_app.env_file
    assert f"RDM_API_KEY='{KEY}'" in env.read_text()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    config = saved_config(make_app)
    assert config["stations"][0]["crs"] == "KGX"
    assert config["stations"][0]["mode"] == "arrivals"
    assert config["announcements"]["enabled"] is True
    assert config["announcements"]["audio_device"] == "jack"
    assert KEY not in make_app.config_file.read_text()
    assert KEY not in response.text
    assert KEY not in caplog.text
    assert os.environ["RDM_API_KEY"] == KEY  # live, with no restart


def test_finishing_clears_the_setup_screen_from_the_state(make_app):
    client = make_app()
    assert client.get("/api/state").json()["setup"]["reason"] == "no_key"

    client.post("/api/setup/complete", json=ANSWERS)

    assert client.get("/api/state").json()["setup"] is None
    assert client.get("/api/state").json()["stations"][0]["crs"] == "KGX"


def test_two_stations_and_announcements_off(make_app):
    client = make_app()

    response = client.post(
        "/api/setup/complete",
        json={
            "stations": [{"crs": "PAD"}, {"crs": "PAD", "mode": "arrivals"}],
            "key": KEY,
            "announce": False,
            "audio_device": "jack",
        },
    )

    assert response.status_code == 200, response.text
    config = saved_config(make_app)
    assert [(s["crs"], s["mode"]) for s in config["stations"]] == [
        ("PAD", "departures"),
        ("PAD", "arrivals"),
    ]
    assert config["announcements"]["enabled"] is False
    assert config["announcements"]["audio_device"] == "hdmi"  # not chosen, so not changed


def test_finishing_leaves_the_profiles_alone(make_app):
    """The board runs a profile; the file must not swallow it (CLAUDE.md, active vs get)."""
    profile = {
        "name": "Evening",
        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "start": "00:00",
        "end": "00:00",  # all day, so the test does not depend on the clock
        "stations": [{"crs": "LBG", "rows": 5}],
        "display": {"theme": "crt"},
    }
    config = Config.model_validate(
        {"stations": [{"crs": "PAD"}], "profiles": {"enabled": True, "entries": [profile]}}
    )
    client = make_app(config=config)
    assert client.get("/api/state").json()["stations"][0]["crs"] == "LBG"  # a profile is active
    before = saved_config(make_app)["profiles"]

    response = client.post("/api/setup/complete", json=ANSWERS)

    assert response.status_code == 200, response.text
    written = saved_config(make_app)
    assert [(s["crs"], s["mode"], s["rows"]) for s in written["stations"]] == [
        ("KGX", "arrivals", 8)  # not the profile's LBG, and not its rows
    ]
    assert written["display"]["theme"] == "modern"  # the profile's crt did not leak in
    assert written["profiles"] == before
    # And the screen still runs the profile: setup edits the base, not the evening.
    assert client.get("/api/state").json()["stations"][0]["crs"] == "LBG"


def test_a_station_already_set_up_keeps_its_other_options(make_app):
    config = Config(stations=[{"crs": "PAD", "rows": 5, "platforms": ["1", "2"], "walk_time": 4}])
    client = make_app(key="old-key", config=config)

    response = client.post(
        "/api/setup/complete", json={"stations": [{"crs": "PAD"}, {"crs": "RDG"}], "key": ""}
    )

    assert response.status_code == 200, response.text
    first, second = saved_config(make_app)["stations"]
    assert (first["rows"], first["platforms"], first["walk_time"]) == (5, ["1", "2"], 4)
    assert (second["crs"], second["rows"], second["platforms"]) == ("RDG", 8, [])


def test_a_blank_key_keeps_the_one_saved(make_app):
    client = make_app(key="old-key")
    make_app.env_file.write_text("RDM_API_KEY='old-key'\n")

    response = client.post("/api/setup/complete", json={**ANSWERS, "key": "   "})

    assert response.status_code == 200, response.text
    assert make_app.env_file.read_text() == "RDM_API_KEY='old-key'\n"
    assert os.environ["RDM_API_KEY"] == "old-key"


def test_a_pasted_key_replaces_the_saved_one(make_app):
    client = make_app(key="old-key")
    make_app.env_file.write_text("OTHER=1\nRDM_API_KEY='old-key'\n")

    client.post("/api/setup/complete", json=ANSWERS)

    text = make_app.env_file.read_text()
    assert "OTHER=1" in text
    assert "old-key" not in text
    assert f"RDM_API_KEY='{KEY}'" in text


def test_no_key_anywhere_is_refused_and_nothing_is_written(make_app):
    client = make_app()
    before = make_app.config_file.read_text()

    response = client.post("/api/setup/complete", json={**ANSWERS, "key": None})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "key"]
    assert not make_app.env_file.exists()
    assert make_app.config_file.read_text() == before


@pytest.mark.parametrize(
    ("answers", "field"),
    [
        ({"stations": [{"crs": "P4D"}]}, "crs"),
        ({"stations": []}, "stations"),
        ({"stations": [{"crs": "PAD"}, {"crs": "RDG"}, {"crs": "KGX"}]}, "stations"),
        ({"stations": [{"crs": "PAD"}, {"crs": "pad"}]}, "stations"),
        ({"audio_device": "bluetooth"}, "audio_device"),
        ({"key": "it's"}, "key"),
    ],
)
def test_a_bad_answer_is_refused_before_anything_is_written(make_app, answers, field):
    client = make_app()
    before = make_app.config_file.read_text()

    response = client.post("/api/setup/complete", json={**ANSWERS, **answers})

    assert response.status_code == 422
    assert any(field in [str(part) for part in e["loc"]] for e in response.json()["detail"])
    assert not make_app.env_file.exists()
    assert make_app.config_file.read_text() == before


def test_finishing_does_not_touch_other_options(make_app):
    config = Config(stations=[{"crs": "PAD"}], display={"theme": "crt"})
    client = make_app(config=config)
    before = saved_config(make_app)

    client.post("/api/setup/complete", json=ANSWERS)

    written = saved_config(make_app)
    for section in ("display", "sources", "schedule", "updates", "weather", "profiles"):
        assert written[section] == before[section], section


# -- the sound test --------------------------------------------------------


def test_the_sound_test_can_try_an_output_that_is_not_saved_yet(make_app, monkeypatch):
    client = make_app()
    calls = []

    async def say(text, audio_device=None):
        calls.append((text, audio_device))

    monkeypatch.setattr(client.app.state.announcer, "say", say)

    client.post("/api/announce/test", json={"audio_device": "jack"})
    client.post("/api/announce/test", json={})

    assert calls[0][1] == "jack"
    assert calls[1][1] is None  # /admin's button, as it was


# -- GET /api/setup/qr.svg -------------------------------------------------


def test_the_qr_code_is_an_svg_that_encodes_the_address_by_number(make_app):
    client = make_app()

    response = client.get("/api/setup/qr.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "no-cache"
    assert response.content.startswith(b"<svg")
    assert response.content == setup.qr_svg(f"http://{LAN_IP}:8080/setup")
    assert response.content != setup.qr_svg("http://describer.local:8080/setup")


def test_the_qr_code_falls_back_to_the_name(make_app, monkeypatch):
    client = make_app()
    monkeypatch.setattr(setup, "lan_ip", lambda: None)

    response = client.get("/api/setup/qr.svg")

    assert response.content == setup.qr_svg("http://describer.local:8080/setup")


def test_the_qr_code_carries_no_colour_of_its_own():
    """The page paints it through a mask, in the theme's colours."""
    svg = setup.qr_svg("http://192.168.1.23:8080/setup").decode()

    assert "fill=" not in svg
    assert "<rect" not in svg  # no background: the panel behind it is the light
