"""HTTP surface: board page, JSON API, config writes and the SSE stream."""

import json

import pytest
from fastapi.testclient import TestClient

from describer.config import Config, save_config
from describer.rail import poller as poller_module
from describer.rail.client import parse_board


@pytest.fixture
def client(monkeypatch, tmp_path, departures_payload):
    board = parse_board(departures_payload, "PAD", "departures")

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def fetch_board(self, crs, mode="departures"):
            return board.model_copy(deep=True)

        async def aclose(self):
            pass

    monkeypatch.setattr(poller_module, "LdbwsClient", FakeClient)
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
    config["api"]["poll_interval"] = 3

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
    assert status["display_on"] is True


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
