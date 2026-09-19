"""Shut down and Restart from /admin. Nothing here can reach systemctl."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from describer import main as main_module
from describer import system
from describer.config import Config, save_config
from describer.rail import sources as sources_module
from describer.system import Power, PowerError


@pytest.fixture(autouse=True)
def instant(monkeypatch):
    monkeypatch.setattr(system, "DELAY", 0)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def recorder():
    ran: list[list[str]] = []

    async def run(args):
        ran.append(list(args))

    return ran, run


async def test_off_a_pi_nothing_runs(power_tripwire):
    ran, run = recorder()
    power = Power(run, available=lambda: False)

    with pytest.raises(PowerError, match="Only on the Pi"):
        power.request("poweroff")
    with pytest.raises(PowerError, match="Only on the Pi"):
        power.request("reboot")
    await settle()

    assert ran == []
    assert power_tripwire == []
    assert power.pending is None


@pytest.mark.parametrize(
    ("action", "command"),
    [("poweroff", ["systemctl", "poweroff"]), ("reboot", ["systemctl", "reboot"])],
)
async def test_on_a_pi_it_asks_systemctl(action, command):
    ran, run = recorder()
    power = Power(run, available=lambda: True)

    power.request(action)
    await settle()

    assert ran == [command]
    assert power.pending == action


async def test_a_second_request_while_one_is_pending_is_refused():
    ran, run = recorder()
    power = Power(run, available=lambda: True)

    power.request("poweroff")
    with pytest.raises(PowerError, match="Already going to poweroff"):
        power.request("reboot")
    await settle()

    assert ran == [["systemctl", "poweroff"]]


async def test_a_refusal_is_reported_and_can_be_retried():
    async def refuse(_args):
        raise PowerError("poweroff failed: Access denied")

    power = Power(refuse, available=lambda: True)

    power.request("poweroff")
    await settle()

    assert power.error == "poweroff failed: Access denied"
    assert power.pending is None
    power.request("reboot")  # not stuck
    assert power.error is None


async def test_the_default_command_is_the_tripwire_here(power_tripwire):
    """Power() with no runner reaches system.run_command, which conftest replaced."""
    power = Power(available=lambda: True)

    power.request("poweroff")
    await settle()

    # The tripwire recorded the call and refused it: the real one never ran.
    assert power_tripwire == [["systemctl", "poweroff"]]


def test_the_real_default_is_the_only_caller_of_systemctl():
    """Guards the guard: the module has one place that spawns a process."""
    import inspect

    source = inspect.getsource(system)
    assert source.count("create_subprocess_exec") == 1
    assert "systemctl" in system.COMMANDS["poweroff"]


@pytest.fixture
def client(monkeypatch, tmp_path, rdm_credentials, departures_payload):
    from describer.rail.ldbws import parse_board

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
    path = tmp_path / "config.yaml"
    save_config(Config(stations=[{"crs": "PAD"}]), path)
    monkeypatch.setenv("DESCRIBER_CONFIG", str(path))
    # A dev machine has no /etc/describer; say so whatever this one has.
    monkeypatch.setattr(system, "on_pi", lambda: False)

    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.mark.parametrize("action", ["poweroff", "reboot"])
def test_endpoints_answer_409_off_a_pi(client, action, power_tripwire):
    response = client.post(f"/api/system/{action}")

    assert response.status_code == 409
    assert response.json()["detail"] == "Only on the Pi"
    assert power_tripwire == []


def test_an_unknown_action_is_not_an_endpoint(client, power_tripwire):
    assert client.post("/api/system/halt").status_code == 422
    assert power_tripwire == []


def test_status_says_whether_power_is_available(client):
    status = client.get("/api/status").json()

    assert status["system"] == {"available": False, "pending": None, "error": None}


def test_endpoint_schedules_the_command_on_a_pi(client, power_tripwire):
    ran, run = recorder()
    client.app.state.power = Power(run, available=lambda: True)

    response = client.post("/api/system/poweroff")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "action": "poweroff"}
    # TestClient's loop is not ours to await; the scheduling is what this proves.
    assert client.app.state.power.pending == "poweroff"
    assert power_tripwire == []
