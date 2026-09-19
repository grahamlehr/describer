"""Powering the Pi off or rebooting it from /admin.

The backend runs as the unprivileged ``describer`` user; a polkit rule
(``deploy/polkit/50-describer.rules``) lets that user ask logind to power off
or reboot, and nothing else. Off a Pi there is nothing to power off, and a
developer's Mac must never be shut down by a click or a test, so every action
answers "Only on the Pi" unless ``/etc/describer`` exists (the same test
``credentials.rtt_allowed`` uses).

The command is injected. Tests hand :class:`Power` a fake, and ``conftest.py``
replaces the real one with a tripwire, so no test can reach ``systemctl``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal

from . import credentials

log = logging.getLogger(__name__)

Action = Literal["poweroff", "reboot"]

#: What each action runs. ``systemctl`` asks logind, which asks polkit.
COMMANDS: dict[Action, list[str]] = {
    "poweroff": ["systemctl", "poweroff"],
    "reboot": ["systemctl", "reboot"],
}
#: Seconds between answering the request and acting, so the answer reaches the
#: browser before the network goes.
DELAY = 1.0
TIMEOUT = 30.0

Runner = Callable[[Sequence[str]], Awaitable[None]]


class PowerError(RuntimeError):
    """A power request that cannot go ahead. The message is for /admin."""


def on_pi() -> bool:
    """True on a deployed Pi, false on a dev machine."""
    return credentials.DEPLOYED_ENV_FILE.parent.is_dir()


async def run_command(args: Sequence[str]) -> None:
    """The real thing: run ``args`` and raise :class:`PowerError` if it fails."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise PowerError(f"{args[-1]} took longer than {TIMEOUT:.0f} s") from exc
    if proc.returncode != 0:
        lines = err.decode(errors="replace").strip().splitlines()
        raise PowerError(f"{args[-1]} failed: {lines[-1] if lines else f'exit {proc.returncode}'}")


class Power:
    """Powers off or reboots, once, and only on a Pi."""

    def __init__(
        self,
        run: Runner | None = None,
        available: Callable[[], bool] = on_pi,
    ) -> None:
        # Looked up at call time so a test's replacement of run_command is used.
        self._run = run
        self._available = available
        self.pending: Action | None = None
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return self._available()

    def request(self, action: Action) -> None:
        """Schedule ``action`` a moment from now, or raise :class:`PowerError`."""
        if not self._available():
            raise PowerError("Only on the Pi")
        if self.pending is not None:
            raise PowerError(f"Already going to {self.pending}")
        self.pending = action
        self.error = None
        log.warning("Power request from /admin: %s", action)
        self._task = asyncio.create_task(self._go(action))

    async def _go(self, action: Action) -> None:
        await asyncio.sleep(DELAY)
        try:
            await (self._run or run_command)(COMMANDS[action])
        except PowerError as exc:
            # Refused (polkit, an inhibitor): say so, and allow another try.
            self.error = str(exc)
            self.pending = None
            log.error("%s", exc)

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def status(self) -> dict[str, object]:
        return {"available": self.available, "pending": self.pending, "error": self.error}
