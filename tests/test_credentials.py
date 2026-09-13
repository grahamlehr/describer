"""Writing the API keys from /admin, and never handing one back."""

import os
import stat
from datetime import UTC, datetime

import pytest

from describer import credentials
from describer.config import SourcesConfig
from describer.rail import sources as sources_module
from describer.rail.sources import SourceManager

WHEN = datetime(2026, 9, 13, 14, 2, tzinfo=UTC)


@pytest.fixture
def on_the_pi(monkeypatch, tmp_path):
    """An env file where the Pi keeps one, and an environment that restores itself."""
    deployed = tmp_path / "etc" / "describer.env"
    deployed.parent.mkdir()
    monkeypatch.setattr(credentials, "DEPLOYED_ENV_FILE", deployed)
    monkeypatch.delenv("DESCRIBER_ENV_FILE", raising=False)
    monkeypatch.delenv("DESCRIBER_ALLOW_RTT_TOKEN", raising=False)
    # write() sets os.environ itself; record both names so teardown undoes it.
    for var in credentials.ENV_NAMES.values():
        monkeypatch.setenv(var, "placeholder")
        monkeypatch.delenv(var)
    return deployed


@pytest.fixture
def on_a_mac(on_the_pi, monkeypatch, tmp_path):
    """No /etc/describer: the repo .env is the file, and RTT is off limits."""
    monkeypatch.setattr(credentials, "DEPLOYED_ENV_FILE", tmp_path / "nowhere" / "describer.env")
    repo_env = tmp_path / "repo.env"
    monkeypatch.setattr(credentials, "REPO_ENV_FILE", repo_env)
    return repo_env


def test_the_pi_writes_where_systemd_reads_last(on_the_pi):
    assert credentials.env_file() == on_the_pi


def test_a_dev_machine_writes_the_repo_env(on_a_mac):
    assert credentials.env_file() == on_a_mac


def test_the_override_wins(on_the_pi, monkeypatch, tmp_path):
    monkeypatch.setenv("DESCRIBER_ENV_FILE", str(tmp_path / "elsewhere.env"))

    assert credentials.env_file() == tmp_path / "elsewhere.env"


def test_a_key_is_written_quoted_stamped_and_private(on_the_pi):
    credentials.write({"rdm": "  abc 123  "}, now=WHEN)

    lines = on_the_pi.read_text().splitlines()
    assert lines == [
        "# RDM_API_KEY set from /admin at 2026-09-13T14:02:00+00:00",
        "RDM_API_KEY='abc 123'",
    ]
    assert stat.S_IMODE(on_the_pi.stat().st_mode) == 0o600
    assert os.environ["RDM_API_KEY"] == "abc 123"


def test_lines_that_are_not_ours_survive(on_the_pi):
    on_the_pi.write_text(
        "# Written by deploy/install.sh. Credentials only — never commit this file.\n"
        "RDM_API_KEY='old'\n"
        "RTT_TOKEN='keep-me'\n"
        "SOMETHING_ELSE=1\n"
    )

    credentials.write({"rdm": "new"}, now=WHEN)

    text = on_the_pi.read_text()
    assert "Written by deploy/install.sh" in text
    assert "RTT_TOKEN='keep-me'" in text
    assert "SOMETHING_ELSE=1" in text
    assert "'old'" not in text
    assert text.count("RDM_API_KEY=") == 1


def test_setting_a_key_twice_leaves_one_line_and_one_stamp(on_the_pi):
    credentials.write({"rdm": "first"}, now=WHEN)
    credentials.write({"rdm": "second"}, now=WHEN)

    text = on_the_pi.read_text()
    assert text.count("RDM_API_KEY=") == 1
    assert text.count("set from /admin") == 1
    assert "'second'" in text


def test_clearing_removes_the_line_rather_than_blanking_it(on_the_pi):
    """A bare RDM_API_KEY= is still an assignment, and once emptied the deployed key."""
    credentials.write({"rdm": "abc", "rtt": "tok"}, now=WHEN)

    credentials.write({}, clear={"rdm"})

    text = on_the_pi.read_text()
    assert "RDM_API_KEY" not in text
    assert "RTT_TOKEN='tok'" in text
    assert "RDM_API_KEY" not in os.environ


@pytest.mark.parametrize("value", ["", "   ", "it's", "two\nlines", "tab\there"])
def test_values_that_cannot_be_written_safely_are_refused(on_the_pi, value):
    with pytest.raises(credentials.CredentialError):
        credentials.write({"rdm": value})

    assert not on_the_pi.exists()


def test_an_unknown_source_is_refused(on_the_pi):
    with pytest.raises(credentials.CredentialError):
        credentials.write({"github": "x"})


def test_a_dev_machine_refuses_an_rtt_token(on_a_mac):
    """Addendum 4: an RTT token on the Mac spends the Pi's allowance."""
    with pytest.raises(credentials.CredentialError, match="allowance"):
        credentials.write({"rtt": "tok"})

    assert "RTT_TOKEN" not in os.environ
    assert not on_a_mac.exists()


def test_a_dev_machine_may_opt_in_to_an_rtt_token(on_a_mac, monkeypatch):
    monkeypatch.setenv("DESCRIBER_ALLOW_RTT_TOKEN", "1")

    credentials.write({"rtt": "tok"})

    assert "RTT_TOKEN='tok'" in on_a_mac.read_text()


def test_a_dev_machine_may_still_set_rdm(on_a_mac):
    credentials.write({"rdm": "abc"})

    assert "RDM_API_KEY='abc'" in on_a_mac.read_text()


def test_status_says_set_and_when_but_never_what(on_the_pi):
    credentials.write({"rdm": "sekrit-value"}, now=WHEN)

    report = credentials.status()

    assert report["keys"] == {
        "rdm": {"set": True, "changed_at": "2026-09-13T14:02:00+00:00"},
        "rtt": {"set": False, "changed_at": None},
    }
    assert report["rtt_allowed"] is True
    assert "sekrit" not in repr(report)


class FakeSource:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False
        self.min_poll_interval = 0
        self.rate_limit: dict[str, int] = {}

    async def fetch_board(self, crs, mode="departures"):  # pragma: no cover - unused
        raise NotImplementedError

    async def aclose(self) -> None:
        self.closed = True


async def test_a_new_primary_key_takes_the_board_back_to_the_primary(
    monkeypatch, rtt_credentials, on_the_pi
):
    monkeypatch.setenv("RTT_TOKEN", "tok")
    built: list[FakeSource] = []

    def make(name):
        def factory(*_a, **_k):
            built.append(FakeSource(name))
            return built[-1]

        return factory

    monkeypatch.setattr(sources_module, "LdbwsClient", make("rdm"))
    monkeypatch.setattr(sources_module, "RttClient", make("rtt"))
    manager = SourceManager(SourcesConfig())
    assert manager.active == "rtt"
    old_rtt = manager._client("rtt")

    credentials.write({"rdm": "abc"})
    manager.credentials_changed()

    assert manager.active == "rdm"
    assert manager.status()["primary_healthy"] is True
    assert manager.status()["credentials"] == {"rdm": True, "rtt": True}
    # The RTT client remembered its token; a changed key must not reuse it.
    assert manager._client("rtt") is not old_rtt


async def test_clearing_the_primary_key_moves_to_the_fallback(
    monkeypatch, rdm_credentials, rtt_credentials, on_the_pi
):
    monkeypatch.setenv("RDM_API_KEY", "abc")
    monkeypatch.setenv("RTT_TOKEN", "tok")
    monkeypatch.setattr(sources_module, "LdbwsClient", lambda *a, **k: FakeSource("rdm"))
    monkeypatch.setattr(sources_module, "RttClient", lambda *a, **k: FakeSource("rtt"))
    manager = SourceManager(SourcesConfig())
    assert manager.active == "rdm"

    credentials.write({}, clear={"rdm"})
    manager.credentials_changed()

    assert manager.active == "rtt"
    assert manager.status()["primary_healthy"] is False
