"""Checking for a newer release and installing it, against local git repos only."""

import asyncio
import subprocess
import sys

import pytest

from describer import updater as updater_module
from describer.config import UpdatesConfig
from describer.updater import UpdateError, Updater

#: Stands in for "import the app and load the config": fails when the release
#: ships a file called ``broken``.
SMOKE = [sys.executable, "-c", "import pathlib, sys; sys.exit(pathlib.Path('broken').exists())"]
#: Stands in for pip: leaves a mark each time it runs.
INSTALL = [sys.executable, "-c", "open('installed', 'a').write('x')"]


def git(cwd, *args) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit(repo, path, text, message) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message)


def publish(dev, path, text, message) -> None:
    commit(dev, path, text, message)
    git(dev, "push", "-q", "origin", "main")


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch, tmp_path):
    """No user or system git config: no signing, no hooks, a known identity."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n"
        "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(updater_module, "RESTART_DELAY", 0)


@pytest.fixture
def repos(tmp_path):
    """A bare 'GitHub', a developer's clone that publishes, and the Pi's clone."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    dev = tmp_path / "dev"
    git(tmp_path, "clone", "-q", str(origin), str(dev))
    publish(dev, "README.md", "one\n", "First")
    pi = tmp_path / "pi"
    git(tmp_path, "clone", "-q", str(origin), str(pi))
    return dev, pi


@pytest.fixture
async def make(repos):
    started: list[Updater] = []

    async def build(**kwargs):
        _dev, pi = repos
        restarts: list[bool] = []

        async def restart():
            restarts.append(True)

        kwargs.setdefault("restart", restart)
        # Nothing under test may reach systemctl, whoever launched pytest.
        kwargs.setdefault("apply_deploy", None)
        kwargs.setdefault("deploy_state", None)
        instance = Updater(
            pi,
            UpdatesConfig(),
            config_path=pi / "config.yaml",
            smoke_command=SMOKE,
            install_command=INSTALL,
            **kwargs,
        )
        await instance.start()
        started.append(instance)
        return instance, restarts

    yield build
    for instance in started:
        await instance.stop()


async def settle() -> None:
    """Let the restart scheduled after an update run."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_start_names_the_running_commit(repos, make):
    _dev, pi = repos
    instance, _ = await make()

    assert instance.running == git(pi, "rev-parse", "--short", "HEAD")
    assert instance.running_subject == "First"


async def test_nothing_waiting(make):
    instance, _ = await make()

    report = await instance.check()

    assert report["count"] == 0
    assert report["available"] == []
    assert report["blocked"] is None
    assert report["checked_at"] is not None


async def test_new_commits_are_listed_newest_first(repos, make):
    dev, _pi = repos
    instance, _ = await make()
    publish(dev, "a.txt", "a\n", "Second")
    publish(dev, "b.txt", "b\n", "Third")

    report = await instance.check()

    assert report["count"] == 2
    assert [c["subject"] for c in report["available"]] == ["Third", "Second"]
    assert report["deploy_changed"] is False
    assert report["requirements_changed"] is False


async def test_a_check_leaves_the_checkout_alone(repos, make):
    dev, pi = repos
    before = git(pi, "rev-parse", "HEAD")
    instance, _ = await make()
    publish(dev, "a.txt", "a\n", "Second")

    await instance.check()

    assert git(pi, "rev-parse", "HEAD") == before


async def test_an_update_fast_forwards_and_restarts(repos, make):
    dev, pi = repos
    instance, restarts = await make()
    publish(dev, "a.txt", "a\n", "Second")

    report = await instance.update()
    await settle()

    assert git(pi, "rev-parse", "HEAD") == git(dev, "rev-parse", "HEAD")
    assert report["count"] == 0
    assert report["last_update"]["to"] == git(pi, "rev-parse", "--short", "HEAD")
    assert report["last_update"]["from"] == instance.running
    assert restarts == [True]
    # Still the old code in memory until the restart lands.
    assert instance.running != report["last_update"]["to"]


async def test_the_restart_closes_the_streams_first(repos, make):
    dev, _pi = repos
    instance, restarts = await make()
    order: list[str] = []
    instance.before_restart = lambda: order.append("streams closed")
    publish(dev, "a.txt", "a\n", "Second")

    await instance.update()
    await settle()

    assert order == ["streams closed"]
    assert restarts == [True]


async def test_nothing_to_install_is_refused(make):
    instance, restarts = await make()

    with pytest.raises(UpdateError, match="up to date"):
        await instance.update()
    await settle()
    assert restarts == []


async def test_local_changes_block_an_update(repos, make):
    dev, pi = repos
    (pi / "README.md").write_text("edited on the Pi\n")
    before = git(pi, "rev-parse", "HEAD")
    instance, restarts = await make()
    publish(dev, "a.txt", "a\n", "Second")

    report = await instance.check()
    assert report["blocked"] == "The checkout has local changes to tracked files"
    assert report["count"] == 1  # still shown, so the reason makes sense

    with pytest.raises(UpdateError, match="local changes"):
        await instance.update()
    assert git(pi, "rev-parse", "HEAD") == before
    assert (pi / "README.md").read_text() == "edited on the Pi\n"
    assert restarts == []


async def test_untracked_files_do_not_block(repos, make):
    """config.yaml, the voices and the venv all live untracked in the checkout."""
    dev, pi = repos
    (pi / "config.yaml").write_text("stations: []\n")
    instance, _ = await make()
    publish(dev, "a.txt", "a\n", "Second")

    await instance.update()

    assert (pi / "config.yaml").read_text() == "stations: []\n"
    assert (pi / "a.txt").exists()


async def test_another_branch_blocks_an_update(repos, make):
    dev, pi = repos
    git(pi, "switch", "-q", "-c", "experiment")
    instance, _ = await make()
    publish(dev, "a.txt", "a\n", "Second")

    report = await instance.check()

    assert report["blocked"] == "The checkout is on experiment, not main"


async def test_a_diverged_checkout_blocks_an_update(repos, make):
    dev, pi = repos
    commit(pi, "local.txt", "x\n", "Made on the Pi")
    instance, _ = await make()
    publish(dev, "a.txt", "a\n", "Second")

    report = await instance.check()

    assert report["blocked"] == "The checkout has commits origin/main does not"


async def test_a_release_touching_deploy_is_flagged(repos, make):
    dev, _pi = repos
    instance, _ = await make()
    publish(dev, "deploy/describer.service", "[Unit]\n", "Change the unit")

    report = await instance.check()

    assert report["deploy_changed"] is True


async def test_changed_requirements_are_installed(repos, make):
    dev, pi = repos
    instance, _ = await make()
    publish(dev, "requirements.txt", "httpx\n", "New dependency")

    report = await instance.check()
    assert report["requirements_changed"] is True
    await instance.update()

    assert (pi / "installed").read_text() == "x"


async def test_unchanged_requirements_are_not_reinstalled(repos, make):
    dev, pi = repos
    instance, _ = await make()
    publish(dev, "a.txt", "a\n", "Second")

    await instance.update()

    assert not (pi / "installed").exists()


async def test_a_release_that_will_not_start_is_rolled_back(repos, make):
    dev, pi = repos
    before = git(pi, "rev-parse", "HEAD")
    instance, restarts = await make()
    publish(dev, "broken", "x\n", "Breaks the app")

    with pytest.raises(UpdateError, match="rolled back"):
        await instance.update()
    await settle()

    assert git(pi, "rev-parse", "HEAD") == before
    assert not (pi / "broken").exists()
    assert restarts == []
    status = instance.status()
    assert status["phase"] == "idle"
    assert "rolled back" in status["last_error"]
    assert status["count"] == 1  # still waiting, for when it is fixed


async def test_a_rollback_reinstalls_the_old_requirements(repos, make):
    dev, pi = repos
    instance, _ = await make()
    commit(dev, "requirements.txt", "httpx\n", "New dependency")
    publish(dev, "broken", "x\n", "And a break")

    with pytest.raises(UpdateError):
        await instance.update()

    # Once for the new requirements, once to put the old ones back.
    assert (pi / "installed").read_text() == "xx"


async def test_without_a_restart_hook_the_update_stays_put(repos, make):
    dev, pi = repos
    instance, _ = await make(restart=None)
    publish(dev, "a.txt", "a\n", "Second")

    report = await instance.update()

    assert report["can_restart"] is False
    assert report["phase"] == "idle"
    assert report["last_update"]["restarting"] is False
    assert (pi / "a.txt").exists()


def with_apply_hook():
    applied: list[bool] = []

    async def apply():
        applied.append(True)

    return applied, apply


async def test_a_release_touching_deploy_starts_the_oneshot_not_a_restart(repos, make):
    dev, _pi = repos
    applied, apply = with_apply_hook()
    instance, restarts = await make(apply_deploy=apply)
    closed: list[str] = []
    instance.before_restart = lambda: closed.append("streams")
    publish(dev, "deploy/describer.service", "[Unit]\n", "Change the unit")

    report = await instance.update()
    await settle()

    assert report["last_update"]["deploy_changed"] is True
    assert report["last_update"]["applying_deploy"] is True
    assert applied == [True]
    # The oneshot restarts the backend itself, so neither the direct restart
    # nor the stream close happens here.
    assert restarts == []
    assert closed == []


async def test_a_release_without_deploy_restarts_directly(repos, make):
    dev, _pi = repos
    applied, apply = with_apply_hook()
    instance, restarts = await make(apply_deploy=apply)
    publish(dev, "a.txt", "a\n", "Second")

    report = await instance.update()
    await settle()

    assert report["last_update"]["applying_deploy"] is False
    assert applied == []
    assert restarts == [True]


async def test_if_the_oneshot_cannot_be_started_it_restarts_anyway(repos, make):
    """An install from before this existed has no such unit; the new code must still start."""
    dev, _pi = repos

    async def missing():
        raise UpdateError("systemctl start failed: Unit describer-apply-deploy.service not found")

    instance, restarts = await make(apply_deploy=missing)
    closed: list[str] = []
    instance.before_restart = lambda: closed.append("streams")
    publish(dev, "deploy/describer.service", "[Unit]\n", "Change the unit")

    await instance.update()
    await settle()

    assert restarts == [True]
    assert closed == ["streams"]
    assert "System files were not applied" in instance.last_error
    assert "not found" in instance.last_error


async def test_without_systemd_deploy_files_are_left_alone(repos, make):
    dev, _pi = repos
    applied, apply = with_apply_hook()
    instance, _ = await make(restart=None, apply_deploy=apply)
    publish(dev, "deploy/describer.service", "[Unit]\n", "Change the unit")

    await instance.update()
    await settle()

    assert applied == []


def test_the_deploy_unit_outcome_is_read_from_systemd():
    parse = updater_module.parse_deploy_state
    base = "LoadState=loaded\nActiveState=inactive\nResult=success\n"

    # Never run since boot, or not installed: nothing to say.
    assert parse(base + "ExecMainStartTimestamp=\n") is None
    assert parse(base + "ExecMainStartTimestamp=n/a\n") is None
    assert parse("LoadState=not-found\nActiveState=inactive\nResult=success\n") is None
    # Ran and finished.
    ran = base + "ExecMainStartTimestamp=Sat 2026-09-19 10:00:00 BST\n"
    assert parse(ran) == {"state": "applied", "result": "success"}
    failed = ran.replace("Result=success", "Result=exit-code")
    assert parse(failed) == {"state": "failed", "result": "exit-code"}
    # Still going.
    going = "LoadState=loaded\nActiveState=activating\nResult=success\n"
    assert parse(going) == {"state": "running", "result": "success"}


async def test_the_status_carries_the_deploy_outcome(make):
    async def shown():
        return (
            "LoadState=loaded\nActiveState=inactive\nResult=exit-code\n"
            "ExecMainStartTimestamp=Sat 2026-09-19 10:00:00 BST\n"
        )

    instance, _ = await make(deploy_state=shown)

    assert instance.status()["deploy"] == {"state": "failed", "result": "exit-code"}


async def test_the_deploy_defaults_are_system_scope(monkeypatch):
    calls = []

    async def fake_run(args, cwd, timeout, env=None):
        calls.append(args)
        return ""

    monkeypatch.setattr(updater_module, "_run", fake_run)

    await updater_module._systemd_apply_deploy(updater_module.Path("."))
    await updater_module._systemd_show_deploy(updater_module.Path("."))

    assert calls[0] == ["systemctl", "start", "--no-block", "describer-apply-deploy.service"]
    assert calls[1][:3] == ["systemctl", "show", "describer-apply-deploy.service"]
    assert all("--user" not in call for call in calls)


async def test_the_default_restart_needs_systemd(repos, monkeypatch):
    _dev, pi = repos
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    off = Updater(pi, UpdatesConfig(), config_path=pi / "config.yaml")
    monkeypatch.setenv("INVOCATION_ID", "abc")
    on = Updater(pi, UpdatesConfig(), config_path=pi / "config.yaml")

    assert off.status()["can_restart"] is False
    assert on.status()["can_restart"] is True


async def test_the_default_restart_is_system_scope(monkeypatch):
    """describer.service is a system unit now: no --user."""
    calls = []

    async def fake_run(args, cwd, timeout, env=None):
        calls.append(args)
        return ""

    monkeypatch.setattr(updater_module, "_run", fake_run)

    await updater_module._systemd_restart(updater_module.Path("."))

    assert calls == [["systemctl", "restart", "--no-block", "describer.service"]]
    assert "--user" not in calls[0]


async def test_an_unreachable_remote_is_reported(repos, make):
    _dev, pi = repos
    git(pi, "remote", "set-url", "origin", str(pi.parent / "gone.git"))
    instance, _ = await make()

    with pytest.raises(UpdateError, match="git fetch"):
        await instance.check()
    assert "git fetch" in instance.status()["last_error"]
    assert instance.status()["phase"] == "idle"


async def test_outside_a_checkout_nothing_is_offered(tmp_path):
    instance = Updater(
        tmp_path, UpdatesConfig(), config_path=tmp_path / "config.yaml", restart=None
    )

    await instance.start()

    assert instance.running is None
    assert instance.status()["blocked"] == "This is not a git checkout"
    await instance.stop()


@pytest.mark.parametrize("field", ["remote", "branch"])
def test_names_passed_to_git_cannot_be_options(field):
    with pytest.raises(ValueError):
        UpdatesConfig(**{field: "--upload-pack=touch /tmp/x"})
