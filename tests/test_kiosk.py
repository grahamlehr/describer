"""The display watchdog. Nothing here can reach systemctl."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from describer import kiosk, system
from describer import main as main_module
from describer.config import Config, save_config
from describer.kiosk import BACKOFF, STALE, KioskError, KioskWatchdog
from describer.rail import sources as sources_module


class Rig:
    """A watchdog on a fake clock, with a fake systemctl."""

    def __init__(self, available: bool = True, active: str = "active") -> None:
        self.now = 1000.0
        self.active = active
        self.ran: list[list[str]] = []
        self.queried: list[list[str]] = []

        async def run(args):
            self.ran.append(list(args))

        async def query(args):
            self.queried.append(list(args))
            return self.active

        self.dog = KioskWatchdog(run, query, available=lambda: available, clock=lambda: self.now)


def test_only_loopback_heartbeats_count():
    rig = Rig()
    for host in ("192.168.1.20", "10.0.0.5", None, "testclient"):
        rig.dog.heartbeat(host)
    assert rig.dog.status()["last_heartbeat_age"] is None
    for host in ("127.0.0.1", "::1", "127.0.1.1"):
        dog = Rig().dog
        dog.heartbeat(host)
        assert dog.status()["last_heartbeat_age"] == 0


async def test_unavailable_does_nothing(power_tripwire):
    rig = Rig(available=False)
    rig.now += STALE * 10
    assert await rig.dog.check() is False
    assert rig.ran == [] and rig.queried == [] and power_tripwire == []


async def test_nothing_within_stale():
    rig = Rig()
    rig.now += STALE
    assert await rig.dog.check() is False
    assert rig.ran == []


async def test_restarts_when_stale_and_active():
    rig = Rig()
    rig.now += STALE + 1
    assert await rig.dog.check() is True
    assert rig.ran == [["systemctl", "restart", "--no-block", "kiosk.service"]]
    assert rig.queried == [["systemctl", "is-active", "kiosk.service"]]
    status = rig.dog.status()
    assert status["restarts"] == 1
    assert status["last_restart_reason"] == "no heartbeat"
    # The restart restarts the clock: no second restart straight away.
    assert await rig.dog.check() is False


@pytest.mark.parametrize("state", ["inactive", "failed", "activating", ""])
async def test_inactive_kiosk_is_left_alone(state):
    rig = Rig(active=state)
    rig.now += STALE + 1
    assert await rig.dog.check() is False
    assert rig.ran == []
    # And a kiosk started by hand gets its full grace period.
    rig.active = "active"
    rig.now += STALE - 1
    assert await rig.dog.check() is False
    rig.now += 2
    assert await rig.dog.check() is True


async def test_backs_off_after_three_restarts():
    rig = Rig()
    for _ in range(3):
        rig.now += STALE + 1
        assert await rig.dog.check() is True
    rig.now += STALE + 1
    assert await rig.dog.check() is False
    rig.now += BACKOFF
    assert await rig.dog.check() is True
    assert len(rig.ran) == 4


async def test_heartbeat_resets_the_backoff():
    rig = Rig()
    for _ in range(3):
        rig.now += STALE + 1
        await rig.dog.check()
    rig.dog.heartbeat("127.0.0.1")
    rig.now += STALE + 1
    assert await rig.dog.check() is True


async def test_failed_restart_is_reported():
    rig = Rig()

    async def refuse(args):
        raise system.PowerError("restart failed: denied")

    rig.dog._run = refuse
    rig.now += STALE + 1
    await rig.dog.check()
    assert rig.dog.status()["error"] == "restart failed: denied"
    rig.dog.heartbeat("127.0.0.1")
    assert rig.dog.status()["error"] is None


async def test_restart_now_unavailable_raises(power_tripwire):
    rig = Rig(available=False)
    with pytest.raises(KioskError, match="Only on the Pi"):
        rig.dog.restart_now()
    assert rig.ran == [] and power_tripwire == []


async def test_restart_now_schedules_a_restart(monkeypatch):
    monkeypatch.setattr(kiosk, "DELAY", 0)
    rig = Rig()
    rig.dog.restart_now()
    with pytest.raises(KioskError, match="Already"):
        rig.dog.restart_now()
    for _ in range(5):
        await asyncio.sleep(0)
    assert rig.ran == [["systemctl", "restart", "--no-block", "kiosk.service"]]
    assert rig.dog.status()["last_restart_reason"] == "admin"
    await rig.dog.stop()


# -- routes ---------------------------------------------------------------------


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
    monkeypatch.setattr(system, "on_pi", lambda: False)

    with TestClient(main_module.app, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def test_heartbeat_route_records_a_loopback_beat(client):
    assert client.get("/api/status").json()["kiosk"]["last_heartbeat_age"] is None
    assert client.post("/api/kiosk/heartbeat").status_code == 204
    assert client.get("/api/status").json()["kiosk"]["last_heartbeat_age"] is not None


def test_heartbeat_route_ignores_other_hosts(client):
    client.app.state.kiosk.heartbeat("192.168.1.9")
    assert client.get("/api/status").json()["kiosk"]["last_heartbeat_age"] is None


def test_restart_route_is_409_off_a_pi(client, power_tripwire):
    response = client.post("/api/kiosk/restart")
    assert response.status_code == 409
    assert response.json()["detail"] == "Only on the Pi"
    assert power_tripwire == []


def test_status_has_a_kiosk_section(client):
    kiosk_status = client.get("/api/status").json()["kiosk"]
    assert kiosk_status["available"] is False
    assert kiosk_status["restarts"] == 0
