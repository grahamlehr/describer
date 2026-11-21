"""Getting the kiosk browser back when the page, not the process, has died.

``kiosk.service`` has ``Restart=always``, which covers Chromium exiting. It
does not cover a crashed renderer (the sad tab), a page stuck in a script or
layout loop, or Chromium's own error page: the browser process is alive, so
systemd sees nothing wrong and the board stays dead until someone reboots.

The board page therefore posts a heartbeat every :data:`HEARTBEAT_SECONDS`
(``board.js``). It proves the page's main thread is running, which an open SSE
stream does not: a hung renderer keeps its socket open. When no heartbeat has
arrived from the Pi itself for :data:`STALE` seconds, this watchdog restarts
``kiosk.service`` (a polkit grant in ``deploy/polkit/50-describer.rules``).

Guards: it only acts on a Pi under systemd, only when the kiosk unit is
``active`` (so a kiosk stopped or disabled by hand stays off), and it backs
off to one attempt every :data:`BACKOFF` seconds after three restarts that
brought no heartbeat back (a screen unplugged, a broken Chromium).

Commands go through :mod:`describer.system`, looked up at call time, so the
tripwire in ``conftest.py`` stops any test reaching ``systemctl``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal

from . import system, updater

log = logging.getLogger(__name__)

UNIT = "kiosk.service"
RESTART_COMMAND = ["systemctl", "restart", "--no-block", UNIT]
IS_ACTIVE_COMMAND = ["systemctl", "is-active", UNIT]

#: How often the board posts a heartbeat. Also in ``board.js``.
HEARTBEAT_SECONDS = 15
CHECK_INTERVAL = 15.0
#: Seconds without a heartbeat before the page is presumed dead.
STALE = 90.0
#: Seconds between attempts once :data:`QUICK_RESTARTS` have not helped.
BACKOFF = 600.0
QUICK_RESTARTS = 3
#: Seconds between answering /admin and restarting.
DELAY = 1.0

Reason = Literal["no heartbeat", "admin"]
Runner = Callable[[Sequence[str]], Awaitable[None]]
Query = Callable[[Sequence[str]], Awaitable[str]]


class KioskError(RuntimeError):
    """A kiosk request that cannot go ahead. The message is for /admin."""


def is_loopback(host: str | None) -> bool:
    """True for the Pi itself: the only client whose heartbeat counts."""
    return host is not None and (host == "::1" or host.startswith("127."))


def can_act() -> bool:
    """True on a deployed Pi under systemd; false on a dev machine."""
    return system.on_pi() and updater.under_systemd()


class KioskWatchdog:
    def __init__(
        self,
        run: Runner | None = None,
        query: Query | None = None,
        available: Callable[[], bool] = can_act,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # run and query are looked up at call time so a test's replacement of
        # the system functions is used.
        self._run = run
        self._query = query
        self._available = available
        self._clock = clock
        self._started_at = clock()
        self._last_heartbeat: float | None = None
        self._last_restart = 0.0
        self._last_inactive = 0.0
        self._streak = 0
        self.restarts = 0
        self.last_restart_reason: Reason | None = None
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._pending: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return self._available()

    def heartbeat(self, host: str | None) -> None:
        """Record a beat from the board, if it came from this machine."""
        if not is_loopback(host):
            return
        self._last_heartbeat = self._clock()
        self._streak = 0
        self.error = None

    def _reference(self) -> float:
        """The latest moment the page could have been given a chance to beat."""
        return max(
            self._last_heartbeat or 0.0, self._started_at, self._last_restart, self._last_inactive
        )

    async def _is_active(self) -> bool:
        out = await (self._query or system.query_command)(IS_ACTIVE_COMMAND)
        return out == "active"

    async def _restart(self, reason: Reason) -> None:
        self._last_restart = self._clock()
        self.restarts += 1
        self.last_restart_reason = reason
        try:
            await (self._run or system.run_command)(RESTART_COMMAND)
        except system.PowerError as exc:
            self.error = str(exc)
            log.error("%s", exc)

    async def check(self) -> bool:
        """One pass. True if it restarted the kiosk."""
        if not self._available():
            return False
        now = self._clock()
        wait = STALE if self._streak < QUICK_RESTARTS else BACKOFF
        if now - self._reference() <= wait:
            return False
        if not await self._is_active():
            # Stopped, disabled or mid-restart: not ours to touch. Start the
            # clock again so a kiosk started by hand gets its full grace.
            self._last_inactive = now
            return False
        log.warning("No heartbeat from the board for %.0f s; restarting %s", wait, UNIT)
        self._streak += 1
        await self._restart("no heartbeat")
        return True

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_INTERVAL)
            try:
                await self.check()
            except Exception:
                log.exception("Kiosk watchdog check failed")

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def restart_now(self) -> None:
        """Restart the kiosk a moment from now, or raise :class:`KioskError`."""
        if not self._available():
            raise KioskError("Only on the Pi")
        if self._pending is not None and not self._pending.done():
            raise KioskError("Already restarting the display")
        log.warning("Display restart from /admin")
        self._streak = 0
        self.error = None
        self._last_restart = self._clock()
        self._pending = asyncio.create_task(self._go())

    async def _go(self) -> None:
        await asyncio.sleep(DELAY)
        await self._restart("admin")

    async def stop(self) -> None:
        for task in (self._task, self._pending):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def status(self) -> dict[str, object]:
        beat = self._last_heartbeat
        return {
            "available": self.available,
            "last_heartbeat_age": None if beat is None else round(self._clock() - beat, 1),
            "restarts": self.restarts,
            "last_restart_reason": self.last_restart_reason,
            "error": self.error,
        }
