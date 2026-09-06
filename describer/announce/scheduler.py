"""Decides which services to announce, and when.

Each service is announced at most once per event type. The record lives in
memory only, so a restart starts the board announcing afresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime

from ..config import Config
from ..rail.models import Board, Service, ServiceStatus
from .phrasing import AnnouncementKind, build_text
from .tts import TtsEngine, TtsError

log = logging.getLogger(__name__)

#: A train already this many seconds past its time is no longer "next to arrive".
GRACE_SECONDS = 60
#: Announcements waiting to be spoken; beyond this we are hopelessly behind.
MAX_QUEUE = 8


class AnnouncementScheduler:
    def __init__(self, engine: TtsEngine) -> None:
        self._engine = engine
        self._announced: set[tuple[str, str]] = set()
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._worker: asyncio.Task[None] | None = None
        self.last_spoken: str | None = None
        self.last_spoken_at: datetime | None = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="describer-announcer")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    # -- decisions ---------------------------------------------------------

    def _pending_kinds(
        self, service: Service, config: Config, now: datetime
    ) -> list[AnnouncementKind]:
        options = config.announcements
        kinds: list[AnnouncementKind] = []

        if service.status is ServiceStatus.CANCELLED:
            if options.announce_cancellations:
                kinds.append(AnnouncementKind.CANCELLED)
            return kinds

        if options.announce_delays and (
            service.status is ServiceStatus.DELAYED or service.delay_minutes >= 1
        ):
            kinds.append(AnnouncementKind.DELAYED)

        remaining = service.seconds_until(now)
        if remaining is not None and -GRACE_SECONDS <= remaining <= options.lead_time:
            kinds.append(AnnouncementKind.ARRIVING)
        return kinds

    async def on_boards(self, boards: list[Board], config: Config) -> None:
        """Poller listener: queue anything newly worth announcing."""
        if not config.announce_any:
            return
        now = datetime.now().astimezone()
        live_ids: set[str] = set()

        for index, board in enumerate(boards):
            if index >= len(config.stations) or not config.stations[index].announce:
                continue
            for service in board.services:
                identity = f"{board.crs}:{board.mode}:{service.id}"
                live_ids.add(identity)
                for kind in self._pending_kinds(service, config, now):
                    if (identity, kind.value) in self._announced:
                        continue
                    self._announced.add((identity, kind.value))
                    text = build_text(
                        kind,
                        service,
                        board.mode,
                        max_calling_points=config.announcements.max_calling_points,
                    )
                    self._enqueue(text)

        self._prune(live_ids)

    def _enqueue(self, text: str) -> None:
        try:
            self._queue.put_nowait(text)
            log.info("Queued announcement: %s", text[:80])
        except asyncio.QueueFull:
            log.warning("Announcement queue full; dropping: %s", text[:60])

    def _prune(self, live_ids: set[str]) -> None:
        """Forget services that have dropped off every board."""
        self._announced = {entry for entry in self._announced if entry[0] in live_ids}

    # -- speaking ----------------------------------------------------------

    async def say(self, text: str) -> None:
        """Speak immediately (used by the admin page's test button)."""
        await self._engine.speak(text)
        self.last_spoken = text
        self.last_spoken_at = datetime.now().astimezone()

    async def _run(self) -> None:
        while True:
            text = await self._queue.get()
            try:
                await self.say(text)
            except TtsError as exc:
                log.error("Announcement failed: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Unexpected announcement error")
            finally:
                self._queue.task_done()
