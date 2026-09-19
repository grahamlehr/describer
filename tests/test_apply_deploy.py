"""deploy/apply-deploy.sh, the root oneshot that installs deploy/ after an update.

No root, no Pi, no network. The script is copied with its paths pointed at a
temp dir and its "GitHub" at a local bare repository; ``systemctl`` and the
few GNU tools macOS lacks are stubs. What is under test is the trust rule: the
checkout, which the unprivileged user can write, gives one commit id and
nothing else, and only a commit on GitHub's main is ever installed.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "apply-deploy.sh"
INSTALL_SH = ROOT / "deploy" / "install.sh"
RULES = ROOT / "deploy" / "polkit" / "50-describer.rules"
UNIT = ROOT / "deploy" / "describer-apply-deploy.service"

GITHUB_URL = "https://github.com/grahamlehr/describer.git"


# -- static ------------------------------------------------------------------


def test_syntax_is_valid():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_shellcheck_if_available():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is not installed")
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)


def test_the_url_is_written_in_the_script_not_read_from_the_checkout():
    text = SCRIPT.read_text()

    assert f"REPO_URL={GITHUB_URL}" in text
    # The checkout's remote is never asked for, and nothing sources anything.
    assert "remote get-url" not in text
    assert not re.search(r"^\s*(source|\.)\s", text, re.MULTILINE)


def test_the_checkout_is_only_ever_asked_for_its_commit():
    """Every git call that names /opt/describer is the one rev-parse HEAD."""
    text = SCRIPT.read_text()
    uses = [line for line in text.splitlines() if "$CHECKOUT" in line and "git" in line]

    assert len(uses) == 1
    assert "rev-parse HEAD" in text.split("checkout_commit() {")[1].split("\n}\n")[0]
    # Nothing from the checkout is executed: no path under it reaches a command.
    assert "$CHECKOUT/" not in text


def test_the_script_replaces_itself_safely():
    """install(1) unlinks the running file; the script must not read on past `exit`."""
    text = SCRIPT.read_text().rstrip().splitlines()

    assert text[-1] == "exit 0"
    assert text[-4] == 'main "$@"'


def test_the_unit_runs_the_installed_copy_as_root():
    text = UNIT.read_text()

    assert "Type=oneshot" in text
    assert "User=root" in text
    assert "ExecStart=/usr/local/sbin/describer-apply-deploy" in text


def test_install_sh_installs_it_root_owned_outside_the_checkout():
    text = INSTALL_SH.read_text()

    assert '-o root -g root "$src/apply-deploy.sh" /usr/local/sbin/describer-apply-deploy' in text
    assert "/etc/systemd/system/describer-apply-deploy.service" in text


def test_provision_reads_everything_it_installs_from_where_it_runs():
    """Root installs from $SCRIPT_DIR, never from $TARGET_DIR (describer's tree)."""
    text = INSTALL_SH.read_text()
    body = text.split("install_units() {")[1].split("\n}\n")[0]
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))

    assert "$TARGET_DIR" not in code
    assert '"$SCRIPT_DIR/config.example.yaml"' in text


def test_provision_runs_nothing_in_the_checkout_as_root():
    """The venv, pip, tar and mkdir inside /opt/describer all go through as_describer."""
    text = INSTALL_SH.read_text()
    body = text.split("build_app() {")[1].split("\n}\n")[0]
    for line in body.splitlines():
        code = line.split("#")[0]
        for tool in ("./.venv/bin/pip", "python3 -m venv", "tar -xz", "mkdir -p"):
            if tool in code:
                assert "as_describer" in code, line
    seed = text.split("seed_config() {")[1].split("\n}\n")[0]
    assert "as_describer" in seed
    assert "chown -R" not in text.split("build_app() {")[1].split("\n}\n")[0]


# -- polkit ------------------------------------------------------------------


def test_polkit_grants_exactly_the_agreed_actions():
    text = RULES.read_text()

    assert 'subject.user != "describer"' in text
    # systemd: restart one unit, start one unit.
    assert text.count("org.freedesktop.systemd1.manage-units") == 1
    assert 'unit == "describer.service" && verb == "restart"' in text
    assert 'unit == "describer-apply-deploy.service" && verb == "start"' in text
    # logind: power off and reboot, with and without other sessions.
    granted = set(re.findall(r'"(org\.freedesktop\.login1\.[a-z-]+)"', text))
    assert granted == {
        "org.freedesktop.login1.power-off",
        "org.freedesktop.login1.power-off-multiple-sessions",
        "org.freedesktop.login1.reboot",
        "org.freedesktop.login1.reboot-multiple-sessions",
    }
    # Nothing is granted by a wildcard, and nothing is a bare YES for any action.
    assert "startsWith" not in text
    assert text.count("polkit.Result.YES") == 3


# -- run against a fake GitHub -------------------------------------------------


def run(cwd, *args, env=None, check=True):
    return subprocess.run(list(args), cwd=cwd, check=check, capture_output=True, text=True, env=env)


class World:
    """A fake GitHub, the Pi's checkout, the deploy clone's home, and stubs."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.github = tmp / "github.git"
        self.dev = tmp / "dev"
        self.checkout = tmp / "opt-describer"
        self.clone = tmp / "deploy-clone"
        self.units = tmp / "units"
        self.bin = tmp / "bin"
        self.stubs = tmp / "stubs"
        self.marker = tmp / "provision-ran.txt"
        self.systemctl_log = tmp / "systemctl.log"
        for d in (self.units, self.bin, self.stubs):
            d.mkdir()
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        (tmp / "gitconfig").write_text(
            "[user]\n\tname = Test\n\temail = t@example.com\n"
            "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n"
        )
        self._stubs()
        run(tmp, "git", "init", "-q", "--bare", "-b", "main", str(self.github), env=self.env)
        run(tmp, "git", "clone", "-q", str(self.github), str(self.dev), env=self.env)
        self.publish("one")
        run(tmp, "git", "clone", "-q", str(self.github), str(self.checkout), env=self.env)

    def _stubs(self) -> None:
        # No GNU coreutils on macOS: stand-ins for the two the script uses.
        (self.stubs / "timeout").write_text('#!/bin/sh\nshift\nexec "$@"\n')
        (self.stubs / "sha256sum").write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
        (self.stubs / "systemctl").write_text(f'#!/bin/sh\necho "$@" >> "{self.systemctl_log}"\n')
        for stub in self.stubs.iterdir():
            stub.chmod(0o755)

    def git(self, cwd, *args):
        return run(cwd, "git", *args, env=self.env).stdout.strip()

    def install_stub(self, label: str) -> str:
        """What GitHub's deploy/install.sh does at this release, in miniature."""
        return f"""#!/usr/bin/env bash
set -e
here="$(cd "$(dirname "$0")/.." && pwd)"
sha="$(git -C "$here" rev-parse HEAD)"
env_seen="frontend=${{DEBIAN_FRONTEND:-unset}} ref=${{DESCRIBER_REF:-unset}}"
env_seen="$env_seen url=${{DESCRIBER_REPO_URL:-unset}}"
echo "$1 $sha $env_seen" > "{self.marker}"
cp "$here/deploy/kiosk.service" "{self.units}/kiosk.service"
# {label}
"""

    def publish(self, label: str, *, kiosk: str = "[Unit]\n") -> str:
        deploy = self.dev / "deploy"
        deploy.mkdir(exist_ok=True)
        (deploy / "install.sh").write_text(self.install_stub(label))
        (deploy / "install.sh").chmod(0o755)
        (deploy / "kiosk.service").write_text(kiosk)
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", label)
        self.git(self.dev, "push", "-q", "origin", "main")
        return self.git(self.dev, "rev-parse", "HEAD")

    def script(self) -> Path:
        text = SCRIPT.read_text()
        swaps = {
            "CHECKOUT=/opt/describer": f"CHECKOUT={self.checkout}",
            "DEPLOY_CLONE=/var/lib/describer-deploy": f"DEPLOY_CLONE={self.clone}",
            f"REPO_URL={GITHUB_URL}": f"REPO_URL={self.github}",
            "  require_root\n  umask": "  umask",
            "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin": (
                f'export PATH="{self.stubs}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"'
            ),
            "/etc/systemd/system/": f"{self.units}/",
            "/usr/local/bin/shutdown-button": f"{self.bin}/shutdown-button",
        }
        for old, new in swaps.items():
            assert old in text, f"apply-deploy.sh no longer contains {old!r}; update this test"
            text = text.replace(old, new)
        path = self.tmp / "apply-deploy.sh"
        path.write_text(text)
        path.chmod(0o755)
        return path

    def apply(self):
        return subprocess.run(
            ["bash", str(self.script())],
            capture_output=True,
            text=True,
            env={"PATH": os.environ["PATH"], "HOME": str(self.tmp)},
        )

    def systemctl_calls(self) -> list[str]:
        if not self.systemctl_log.exists():
            return []
        return self.systemctl_log.read_text().splitlines()


@pytest.fixture
def world(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    return World(tmp_path)


def test_a_commit_on_main_is_installed_from_the_root_clone(world):
    tip = world.publish("two", kiosk="[Unit]\nDescription=two\n")
    world.git(world.checkout, "pull", "-q", "--ff-only")

    done = world.apply()

    assert done.returncode == 0, done.stderr
    ran = world.marker.read_text()
    # provision ran, from the clone, at exactly the checkout's commit, with a
    # clean environment (no DESCRIBER_REF, no repo URL) and no prompts.
    assert ran.startswith(f"provision {tip} ")
    assert "frontend=noninteractive" in ran
    assert "ref=unset url=unset" in ran
    assert world.git(world.clone, "rev-parse", "HEAD") == tip
    # The clone came from the script's URL.
    assert world.git(world.clone, "remote", "get-url", "origin") == str(world.github)
    calls = world.systemctl_calls()
    assert "daemon-reload" in calls
    assert calls[-1] == "restart --no-block describer.service"
    # kiosk.service changed (it did not exist before), so kiosk restarts.
    assert "restart kiosk.service" in calls


def test_unchanged_units_are_not_restarted(world):
    world.git(world.checkout, "pull", "-q", "--ff-only")
    assert world.apply().returncode == 0
    world.systemctl_log.unlink()

    # Same units, a newer release.
    world.publish("two")
    world.git(world.checkout, "pull", "-q", "--ff-only")
    done = world.apply()

    assert done.returncode == 0, done.stderr
    calls = world.systemctl_calls()
    assert "restart kiosk.service" not in calls
    assert "restart shutdown-button.service" not in calls
    assert calls[-1] == "restart --no-block describer.service"


def test_an_older_commit_of_main_is_accepted(world):
    """Documented in the threat model: a downgrade to main's history is allowed."""
    old = world.git(world.dev, "rev-parse", "HEAD")
    world.publish("two")
    world.git(world.checkout, "pull", "-q", "--ff-only")
    world.git(world.checkout, "reset", "-q", "--hard", old)

    assert world.apply().returncode == 0
    assert world.marker.read_text().startswith(f"provision {old} ")


def test_a_commit_that_is_not_in_github_is_refused(world):
    """describer-owned checkout holds a commit of its own, with its own install.sh."""
    deploy = world.checkout / "deploy"
    (deploy / "install.sh").write_text(f'#!/bin/sh\necho evil > "{world.tmp}/evil"\n')
    world.git(world.checkout, "commit", "-q", "-am", "evil")

    done = world.apply()

    assert done.returncode != 0
    assert "REFUSING" in done.stderr
    assert not world.marker.exists()
    assert not (world.tmp / "evil").exists()
    # It still brings the backend back up: nothing about that is privileged.
    assert world.systemctl_calls()[-1] == "restart --no-block describer.service"
    assert "daemon-reload" not in world.systemctl_calls()


def test_a_commit_on_another_branch_of_github_is_refused(world):
    """Being in GitHub is not enough: it must be main, or an ancestor of it."""
    world.git(world.dev, "checkout", "-q", "-b", "evil")
    (world.dev / "x").write_text("x")
    world.git(world.dev, "add", "x")
    world.git(world.dev, "commit", "-q", "-m", "not on main")
    world.git(world.dev, "push", "-q", "origin", "evil")
    world.git(world.checkout, "fetch", "-q", "origin", "evil")
    world.git(world.checkout, "reset", "-q", "--hard", "FETCH_HEAD")

    done = world.apply()

    assert done.returncode != 0
    assert "is not on main" in done.stderr
    assert not world.marker.exists()


def test_the_checkouts_own_files_and_remote_are_never_used(world):
    """A hostile checkout: another remote, an edited install.sh, planted config."""
    tip = world.git(world.checkout, "rev-parse", "HEAD")
    evil = world.tmp / "evil.git"
    run(world.tmp, "git", "init", "-q", "--bare", str(evil), env=world.env)
    world.git(world.checkout, "remote", "set-url", "origin", str(evil))
    # Untracked and modified files in the tree change nothing about what runs.
    (world.checkout / "deploy" / "install.sh").write_text(
        f'#!/bin/sh\necho evil > "{world.tmp}/evil"\n'
    )
    (world.checkout / "deploy" / "apply-deploy.sh").write_text("exit 0\n")
    world.git(world.checkout, "config", "core.fsmonitor", f"touch {world.tmp}/fsmonitor")
    world.git(world.checkout, "config", "core.pager", f"touch {world.tmp}/pager")

    done = world.apply()

    assert done.returncode == 0, done.stderr
    assert world.marker.read_text().startswith(f"provision {tip} ")
    assert world.git(world.clone, "remote", "get-url", "origin") == str(world.github)
    assert not (world.tmp / "evil").exists()
    assert not (world.tmp / "fsmonitor").exists()
    assert not (world.tmp / "pager").exists()


def test_a_checkout_that_is_not_a_repository_is_refused(world):
    shutil.rmtree(world.checkout / ".git")

    done = world.apply()

    assert done.returncode != 0
    assert not world.marker.exists()


def test_a_failing_provision_still_restarts_the_backend_and_reports_failure(world):
    """The unit's Result must read as a failure, and the board must not be left on old code."""
    world.publish("two")
    world.git(world.checkout, "pull", "-q", "--ff-only")
    # A release whose install.sh fails.
    (world.dev / "deploy" / "install.sh").write_text("#!/bin/sh\nexit 3\n")
    world.git(world.dev, "commit", "-q", "-am", "broken")
    world.git(world.dev, "push", "-q", "origin", "main")
    world.git(world.checkout, "pull", "-q", "--ff-only")

    done = world.apply()

    assert done.returncode != 0
    calls = world.systemctl_calls()
    assert "daemon-reload" not in calls
    assert calls[-1] == "restart --no-block describer.service"
