"""HTTP surface: board page, JSON API, config writes and the SSE stream."""

import json

import pytest
from fastapi.testclient import TestClient

from describer import main as main_module
from describer.config import Config, save_config
from describer.rail import sources as sources_module
from describer.rail.ldbws import parse_board


@pytest.fixture
def client(monkeypatch, tmp_path, rdm_credentials, departures_payload):
    board = parse_board(departures_payload, "PAD", "departures")

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def fetch_board(self, crs, mode="departures"):
            return board.model_copy(deep=True)

        async def aclose(self):
            pass

    monkeypatch.setattr(sources_module, "LdbwsClient", FakeClient)
    # Startup calls load_dotenv, which would put a developer's real .env back
    # over what clean_credentials just took away. The tests own the environment.
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: None)
    path = tmp_path / "config.yaml"
    save_config(Config(stations=[{"crs": "PAD"}]), path)
    monkeypatch.setenv("DESCRIBER_CONFIG", str(path))

    from describer.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_board_page_is_served(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "board-template" in response.text


def test_admin_page_is_served(client):
    assert "Describer" in client.get("/admin").text


def test_static_assets_are_served(client):
    assert client.get("/static/themes/splitflap.css").status_code == 200
    assert client.get("/static/themes/1990s.css").status_code == 200
    assert client.get("/static/themes/1990s.js").status_code == 200
    assert client.get("/static/themes/nse.css").status_code == 200
    assert client.get("/static/themes/nse.js").status_code == 200
    assert client.get("/static/themes/led-matrix.css").status_code == 200
    assert client.get("/static/themes/led-matrix.js").status_code == 200
    assert client.get("/static/themes/dotmatrix.js").status_code == 200
    assert client.get("/static/themes/thameslink.css").status_code == 200
    assert client.get("/static/themes/thameslink.js").status_code == 200


def test_static_assets_must_be_revalidated(client):
    """A kiosk is never hard-refreshed, so nothing may go stale in its cache."""
    for path in ("/", "/admin", "/static/board.js", "/static/themes/nse.css"):
        response = client.get(path)
        assert response.headers["cache-control"] == "no-cache", path
        # Revalidation has to be cheap, which means an ETag to revalidate with.
        if path.startswith("/static/"):
            assert response.headers["etag"]


def test_state_endpoint(client):
    state = client.get("/api/state").json()

    assert state["stations"][0]["crs"] == "PAD"
    assert len(state["boards"]) == 1


def test_config_round_trips_through_the_api(client, tmp_path):
    config = client.get("/api/config").json()
    config["display"]["theme"] = "crt"
    config["stations"][0]["rows"] = 6

    response = client.put("/api/config", json=config)

    assert response.status_code == 200
    assert response.json()["display"]["theme"] == "crt"
    # Written straight through to the file the board reads.
    assert "theme: crt" in (tmp_path / "config.yaml").read_text()
    assert client.get("/api/state").json()["display"]["theme"] == "crt"


def test_invalid_config_is_rejected_with_field_errors(client):
    config = client.get("/api/config").json()
    config["sources"]["poll_interval"] = 3

    response = client.put("/api/config", json=config)

    assert response.status_code == 422
    assert any("poll_interval" in str(error["loc"]) for error in response.json()["detail"])


def test_invalid_config_is_not_persisted(client, tmp_path):
    config = client.get("/api/config").json()
    config["stations"][0]["crs"] = "TOOLONG"
    client.put("/api/config", json=config)

    assert "TOOLONG" not in (tmp_path / "config.yaml").read_text()


def test_status_reports_the_feed_and_tts(client):
    status = client.get("/api/status").json()

    assert set(status["tts"]) == {"piper", "voice", "player"}
    assert status["boards"][0]["crs"] == "PAD"
    assert status["boards"][0]["source"] == "rdm"
    assert status["display_on"] is True
    assert status["display_mode"] in (None, "auto")


def test_status_reports_the_sources(client):
    status = client.get("/api/status").json()

    assert status["active_source"] == "rdm"
    assert status["forced_source"] == "auto"
    assert status["primary_healthy"] is True
    assert status["credentials"] == {"rdm": True, "rtt": False}
    assert "api_key_present" not in status  # replaced by the credentials map


def test_source_can_be_forced_without_touching_the_config(client, tmp_path):
    forced = client.post("/api/source/force", json={"source": "rtt"}).json()

    assert forced["active_source"] == "rtt"
    assert forced["forced_source"] == "rtt"
    assert "rtt" not in (tmp_path / "config.yaml").read_text().split("sources:")[0]
    assert client.get("/api/status").json()["forced_source"] == "rtt"

    back = client.post("/api/source/force", json={"source": "auto"}).json()
    assert back["forced_source"] == "auto"
    assert back["active_source"] == "rdm"


def test_unknown_forced_source_is_rejected(client):
    assert client.post("/api/source/force", json={"source": "nre"}).status_code == 422


PROFILE = {
    "name": "Evening",
    "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "start": "00:00",
    "end": "00:00",  # all day, so the test does not depend on the clock
    "stations": [{"crs": "LBG"}],
    "display": {"theme": "crt"},
}


def save_profile(client):
    config = client.get("/api/config").json()
    config["profiles"] = {"enabled": True, "entries": [PROFILE]}
    response = client.put("/api/config", json=config)
    assert response.status_code == 200, response.text
    return response


def test_the_config_api_serves_the_file_while_the_board_runs_the_profile(client):
    """/admin edits what is on disk; /api/state shows what is on the screen."""
    save_profile(client)

    assert client.get("/api/config").json()["stations"][0]["crs"] == "PAD"
    state = client.get("/api/state").json()
    assert state["stations"][0]["crs"] == "LBG"
    assert state["display"]["theme"] == "crt"


def test_status_names_the_profile_in_force(client):
    save_profile(client)

    status = client.get("/api/status").json()

    assert status["active_profile"] == "Evening"
    assert status["forced_profile"] is None
    # One profile covering every hour of every day never hands over.
    assert status["next_profile"] is None


def test_a_profile_can_be_forced_without_touching_the_config(client, tmp_path):
    config = client.get("/api/config").json()
    config["profiles"] = {"enabled": False, "entries": [PROFILE]}
    client.put("/api/config", json=config)
    assert client.get("/api/state").json()["stations"][0]["crs"] == "PAD"

    forced = client.post("/api/profile/force", json={"profile": "Evening"})

    assert forced.json()["forced_profile"] == "Evening"
    assert client.get("/api/state").json()["stations"][0]["crs"] == "LBG"
    # Pinning is for testing out of hours: it must not reach the file.
    assert "enabled: false" in (tmp_path / "config.yaml").read_text()

    client.post("/api/profile/force", json={"profile": None})
    assert client.get("/api/state").json()["stations"][0]["crs"] == "PAD"


def test_forcing_a_profile_that_does_not_exist_is_rejected(client):
    assert client.post("/api/profile/force", json={"profile": "Nope"}).status_code == 404


def test_a_profile_that_cannot_merge_is_refused_with_field_errors(client):
    config = client.get("/api/config").json()
    config["profiles"] = {
        "enabled": True,
        "entries": [{**PROFILE, "display": {"themes": {"crt": {"phosphor": "purple"}}}}],
    }

    response = client.put("/api/config", json=config)

    assert response.status_code == 422
    assert "Evening" in json.dumps(response.json()["detail"])


def test_test_announcement_reports_a_missing_piper(client):
    response = client.post("/api/announce/test", json={"text": "Hello"})

    # No Piper on the test host: the admin page must be told why, not left guessing.
    assert response.status_code == 503
    assert "Piper" in response.json()["detail"]


async def test_stream_frames_are_sse_formatted():
    """TestClient runs a streaming endpoint to completion, so drive the
    generator directly: it is the same code path uvicorn uses."""
    from starlette.requests import Request

    from describer.config import Config, ConfigStore
    from describer.main import api_stream, app
    from describer.rail.poller import Poller

    poller = Poller(ConfigStore(Config(stations=[{"crs": "PAD"}])))
    app.state.poller = poller
    scope = {"type": "http", "method": "GET", "path": "/api/stream", "headers": [], "app": app}

    response = await api_stream(Request(scope))
    frames = response.body_iterator

    first = await anext(frames)
    assert first.startswith("data: ") and first.endswith("\n\n")
    assert json.loads(first[6:])["boards"][0]["crs"] == "PAD"

    poller._publish({"type": "state", "boards": []})
    assert json.loads((await anext(frames))[6:])["type"] == "state"

    await frames.aclose()
    assert not poller._subscribers  # the disconnect unsubscribed us


def test_sse_frame_format():
    from describer.main import _sse

    assert _sse({"a": 1}) == 'data: {"a":1}\n\n'
