"""Noticing a newer release on GitHub, and installing it from /admin.

The Pi's checkout is a clone of the public repository, so a check is a
``git fetch`` and needs no token and no GitHub API allowance. Updates are
offered, never installed on their own: /admin shows what is waiting and the
user presses the button.

An update fast-forwards the checkout, installs requirements if they changed,
and then proves the new code will start before restarting into it — imports
the app and loads the current config in a fresh interpreter. A broken restart
would take /admin down with it, and /admin is the only way back without SSH,
so any failure before the restart resets the checkout to where it was.

What an update cannot do is anything under ``deploy/``: the unit files are
copied into place by ``install.sh`` with sudo. /admin says when a release
touches them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import UpdatesConfig

log = logging.getLogger(__name__)

#: Seconds after startup before the first check. A Pi that has just booted has
#: better things to do, and a test app never lives this long.
STARTUP_DELAY = 60.0
#: Seconds between answering /api/updates/apply and restarting, so the answer
#: reaches the browser before the process goes.
RESTART_DELAY = 1.0
#: The backend's own systemd unit.
UNIT = "describer.service"
FETCH_TIMEOUT = 60.0
INSTALL_TIMEOUT = 900.0
SMOKE_TIMEOUT = 120.0
#: Commit subjects listed in /admin; the count is always exact.
MAX_COMMITS = 50

RestartHook = Callable[[], Awaitable[None]]


class UpdateError(RuntimeError):
    """A check or an update that could not go ahead. The message is for /admin."""


def under_systemd() -> bool:
    """True when systemd started us, and will start us again after a restart."""
    return bool(os.environ.get("INVOCATION_ID"))


async def _run(
    args: Sequence[str], cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> str:
    """Run a command to completion; its stdout, or :class:`UpdateError`."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})},
    )
    what = " ".join(Path(args[0]).name if i == 0 else a for i, a in enumerate(args[:3]))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise UpdateError(f"{what} took longer than {timeout:.0f} s") from exc
    if proc.returncode != 0:
        lines = (err or out).decode(errors="replace").strip().splitlines()
        raise UpdateError(f"{what} failed: {lines[-1] if lines else f'exit {proc.returncode}'}")
    return out.decode(errors="replace").strip()


async def _systemd_restart(repo: Path) -> None:
    # System scope, not --user: describer.service is a system unit now. A
    # polkit rule lets the describer user run exactly this restart.
    # --no-block: the restart stops this very process, so do not wait on it.
    await _run(["systemctl", "restart", "--no-block", UNIT], repo, 30)


_SYSTEMD = object()


class Updater:
    """Checks for new commits on the configured branch and installs them."""

    def __init__(
        self,
        repo: Path,
        config: UpdatesConfig,
        *,
        config_path: Path,
        python: str = sys.executable,
        smoke_command: Sequence[str] | None = None,
        install_command: Sequence[str] | None = None,
        restart: RestartHook | None | object = _SYSTEMD,
    ) -> None:
        self._repo = repo
        self._config = config
        self._smoke = list(
            smoke_command
            or [
                python,
                "-c",
                "import pathlib, sys, describer.main; from describer.config import load_config;"
                " load_config(pathlib.Path(sys.argv[1]))",
                str(config_path),
            ]
        )
        self._install = list(
            install_command or [python, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"]
        )
        if restart is _SYSTEMD:
            self._restart: RestartHook | None = (
                (lambda: _systemd_restart(repo)) if under_systemd() else None
            )
        else:
            self._restart = restart  # type: ignore[assignment]
        #: Called just before a restart, to close what would hold the shutdown up.
        self.before_restart: Callable[[], None] | None = None

        #: The commit this process is running. Fixed at startup: after an update
        #: HEAD moves on, but the code in memory does not until the restart.
        self.running: str | None = None
        self.running_subject: str | None = None
        self.checked_at: datetime | None = None
        self.available: list[dict[str, str]] = []
        self.count = 0
        self.blocked: str | None = None
        self.deploy_changed = False
        self.requirements_changed = False
        self.phase = "idle"
        self.last_error: str | None = None
        self.last_update: dict[str, Any] | None = None
        self._upstream: str | None = None
        self._noticed: str | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._restart_task: asyncio.Task[None] | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        try:
            self.running = await self._git("rev-parse", "--short", "HEAD")
            self.running_subject = await self._git("log", "-1", "--format=%s")
        except UpdateError as exc:
            self.blocked = "This is not a git checkout"
            log.warning("Update checks unavailable: %s", exc)
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def update_config(self, config: UpdatesConfig) -> None:
        """Adopt a new config from /admin; the loop reads it on each pass."""
        self._config = config

    async def _loop(self) -> None:
        await asyncio.sleep(STARTUP_DELAY)
        while True:
            if self._config.enabled:
                try:
                    await self.check()
                except UpdateError as exc:
                    log.warning("Update check failed: %s", exc)
            await asyncio.sleep(self._config.check_interval)

    # -- git -----------------------------------------------------------------

    async def _git(self, *args: str, timeout: float = 30.0) -> str:
        # No prompt for credentials on a public remote, and messages in English
        # so the last line of an error reads the same everywhere.
        return await _run(
            ["git", *args], self._repo, timeout, env={"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        )

    @property
    def _tracking(self) -> str:
        return f"refs/remotes/{self._config.remote}/{self._config.branch}"

    async def _blocker(self, upstream: str) -> str | None:
        """Why this checkout cannot simply be fast-forwarded, if it cannot."""
        branch = self._config.branch
        try:
            current = await self._git("symbolic-ref", "--short", "-q", "HEAD")
        except UpdateError:
            current = None
        if current != branch:
            return f"The checkout is on {current or 'a detached HEAD'}, not {branch}"
        # Untracked files are left alone: the voices, the venv and config.yaml
        # all live in the checkout, and a merge that would clobber one fails
        # and is rolled back like any other.
        if await self._git("status", "--porcelain", "--untracked-files=no"):
            return "The checkout has local changes to tracked files"
        if int(await self._git("rev-list", "--count", f"{upstream}..HEAD")):
            return f"The checkout has commits {self._config.remote}/{branch} does not"
        return None

    # -- checking ------------------------------------------------------------

    async def check(self) -> dict[str, Any]:
        """Fetch and compare. Leaves the checkout itself untouched."""
        if self._lock.locked():
            return self.status()  # an update is running, and checks as it goes
        async with self._lock:
            await self._check()
        return self.status()

    async def _check(self) -> None:
        self.phase = "checking"
        try:
            remote, branch = self._config.remote, self._config.branch
            await self._git(
                "fetch",
                "--quiet",
                remote,
                f"+refs/heads/{branch}:{self._tracking}",
                timeout=FETCH_TIMEOUT,
            )
            upstream = await self._git("rev-parse", self._tracking)
            self.count = int(await self._git("rev-list", "--count", f"HEAD..{upstream}"))
            listed = await self._git(
                "log", f"--max-count={MAX_COMMITS}", "--format=%h%x09%s", f"HEAD..{upstream}"
            )
            self.available = [
                dict(zip(("sha", "subject"), line.split("\t", 1), strict=False))
                for line in listed.splitlines()
                if line
            ]
            changed = (
                (await self._git("diff", "--name-only", "HEAD", upstream)).splitlines()
                if self.count
                else []
            )
            self.deploy_changed = any(path.startswith("deploy/") for path in changed)
            self.requirements_changed = "requirements.txt" in changed
            self.blocked = await self._blocker(upstream)
            self._upstream = upstream
            self.checked_at = datetime.now().astimezone()
            self.last_error = None
            if self.count and upstream != self._noticed:
                self._noticed = upstream
                log.info("%d update(s) available from %s/%s", self.count, remote, branch)
        except UpdateError as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.phase = "idle"

    # -- updating ------------------------------------------------------------

    async def update(self) -> dict[str, Any]:
        """Install what is waiting, prove it starts, then restart into it."""
        if self._lock.locked():
            raise UpdateError("A check or an update is already running")
        async with self._lock:
            await self._check()
            if self.blocked:
                raise UpdateError(self.blocked)
            if not self.count or self._upstream is None:
                raise UpdateError("Already up to date")

            old = await self._git("rev-parse", "HEAD")
            requirements = self.requirements_changed
            self.phase = "updating"
            try:
                await self._git("merge", "--ff-only", "--quiet", self._upstream)
                if requirements:
                    await _run(self._install, self._repo, INSTALL_TIMEOUT)
                await _run(self._smoke, self._repo, SMOKE_TIMEOUT)
            except UpdateError as exc:
                log.error("Update failed, rolling back: %s", exc)
                await self._rollback(old, requirements)
                self.last_error = f"{exc} (rolled back; nothing was restarted)"
                self.phase = "idle"
                raise UpdateError(self.last_error) from exc

            new = await self._git("rev-parse", "--short", "HEAD")
            self.last_update = {
                "from": self.running,
                "to": new,
                "at": datetime.now().astimezone().isoformat(),
                "commits": self.count,
                "deploy_changed": self.deploy_changed,
                "restarting": self._restart is not None,
            }
            self.available, self.count = [], 0
            self.last_error = None
            log.info("Updated %s -> %s", self.running, new)
            if self._restart is None:
                self.phase = "idle"
            else:
                self.phase = "restarting"
                self._restart_task = asyncio.create_task(self._restart_soon())
            return self.status()

    async def _rollback(self, old: str, requirements: bool) -> None:
        try:
            # The tree held no tracked changes (the check refuses otherwise), so
            # this discards only what the merge brought in.
            await self._git("reset", "--hard", "--quiet", old)
            if requirements:
                await _run(self._install, self._repo, INSTALL_TIMEOUT)
        except UpdateError as exc:
            log.error("Rollback incomplete: %s", exc)

    async def _restart_soon(self) -> None:
        await asyncio.sleep(RESTART_DELAY)
        if self.before_restart is not None:
            self.before_restart()
        try:
            await self._restart()  # type: ignore[misc]
        except UpdateError as exc:
            self.phase = "idle"
            self.last_error = f"Installed, but the restart failed: {exc}"
            log.error("%s", self.last_error)

    # -- reporting -----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "tracking": f"{self._config.remote}/{self._config.branch}",
            "running": self.running,
            "running_subject": self.running_subject,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "count": self.count,
            "available": self.available,
            "blocked": self.blocked,
            "deploy_changed": self.deploy_changed,
            "requirements_changed": self.requirements_changed,
            "phase": self.phase,
            "can_restart": self._restart is not None,
            "last_error": self.last_error,
            "last_update": self.last_update,
        }
