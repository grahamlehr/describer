"""Decides which services to announce, and when.

Each service is announced at most once per event type. The record lives in
memory only, so a restart starts the board announcing afresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from ..config import Config
from ..rail.models import Board, Service, ServiceStatus
from .phrasing import AnnouncementKind, build_text
from .tts import TtsEngine, TtsError

log = logging.getLogger(__name__)

#: A train already this many seconds past its time is no longer "next to arrive".
GRACE_SECONDS = 60
#: Announcements waiting to be spoken; beyond this we are hopelessly behind.
MAX_QUEUE = 8
#: How long a train is remembered after it was last seen on a board.
#:
#: Forgetting a train the moment it leaves the board is wrong once profiles
#: exist: a station that goes away at 09:30 and comes back at 16:30 would have
#: every one of its trains announced a second time. It is remembered for long
#: enough that nothing announced can come round again, and no longer.
FORGET_AFTER = timedelta(minutes=30)


class AnnouncementScheduler:
    def __init__(self, engine: TtsEngine) -> None:
        self._engine = engine
        #: (train, event) -> when that train was last seen on a board.
        self._announced: dict[tuple[str, str], datetime] = {}
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

        for index, board in enumerate(boards):
            if index >= len(config.stations) or not config.stations[index].announce:
                continue
            for service in board.services:
                # Keyed on the train, not on Service.id: the two sources number
                # the same train differently, and a failover must not re-announce it.
                identity = (
                    f"{board.crs}:{board.mode}:{service.scheduled_time}:{service.destination}"
                )
                # Still on the board, so it is still worth remembering.
                for kind in AnnouncementKind:
                    if (identity, kind.value) in self._announced:
                        self._announced[(identity, kind.value)] = now
                for kind in self._pending_kinds(service, config, now):
                    if (identity, kind.value) in self._announced:
                        continue
                    self._announced[(identity, kind.value)] = now
                    text = build_text(
                        kind,
                        service,
                        board.mode,
                        max_calling_points=config.announcements.max_calling_points,
                    )
                    self._enqueue(text)

        self._prune(now)

    def _enqueue(self, text: str) -> None:
        try:
            self._queue.put_nowait(text)
            log.info("Queued announcement: %s", text[:80])
        except asyncio.QueueFull:
            log.warning("Announcement queue full; dropping: %s", text[:60])

    def _prune(self, now: datetime) -> None:
        """Forget services no board has shown for a while.

        Pruned on age rather than on absence: a station can leave the board and
        come back within the day, and its trains must not be announced twice.
        """
        self._announced = {
            key: seen for key, seen in self._announced.items() if now - seen < FORGET_AFTER
        }

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
