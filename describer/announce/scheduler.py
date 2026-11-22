"""Decides which services to announce, and when.

Each service is announced at most once per event type. The record lives in
memory only, so a restart starts the board announcing afresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import Config, StationConfig
from ..rail.models import Board, Service, ServiceStatus
from .clips import Part
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


#: The /admin test button's words, which a voice pack carries as one clip.
TEST_TEXT = "This is a test announcement from the departure board."


@dataclass(frozen=True)
class Announcement:
    """One thing to say: the words, and what they were made from.

    The service rides along so the recorded voice can plan its clips when the
    announcement is spoken, against whichever pack is installed by then.
    """

    text: str
    kind: AnnouncementKind | None = None
    service: Service | None = None
    mode: str = "departures"
    max_calling_points: int = 8
    #: The station to name in an arrival, set only when two stations announce.
    station: str | None = None
    station_crs: str | None = None


class AnnouncementScheduler:
    def __init__(self, engine: TtsEngine) -> None:
        self._engine = engine
        #: (train, event) -> when that train was last seen on a board.
        self._announced: dict[tuple[str, str], datetime] = {}
        self._queue: asyncio.Queue[Announcement] = asyncio.Queue(maxsize=MAX_QUEUE)
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
        self,
        service: Service,
        config: Config,
        station: StationConfig,
        now: datetime,
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

        # A walk time takes the train off the board before it is close, so
        # the lead has to reach at least that far or the announcement never
        # comes: the last useful moment to call a train you have to walk to
        # is the moment it stops being catchable.
        lead = max(options.lead_time, station.walk_time * 60)
        remaining = service.seconds_until(now)
        if remaining is not None and -GRACE_SECONDS <= remaining <= lead:
            kinds.append(AnnouncementKind.ARRIVING)
        return kinds

    async def on_boards(self, boards: list[Board], config: Config) -> None:
        """Poller listener: queue anything newly worth announcing."""
        if not config.announce_any:
            return
        now = datetime.now().astimezone()
        # With one station announcing, "platform one" can only mean one place.
        # With two it cannot, so each arrival says which station it is for.
        announcing = sum(
            1
            for index in range(min(len(boards), len(config.stations)))
            if config.stations[index].announce
        )
        name_station = announcing >= 2

        for index, board in enumerate(boards):
            if index >= len(config.stations) or not config.stations[index].announce:
                continue
            station = config.stations[index]
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
                for kind in self._pending_kinds(service, config, station, now):
                    if (identity, kind.value) in self._announced:
                        continue
                    self._announced[(identity, kind.value)] = now
                    named = board.name if name_station and board.name else None
                    text = build_text(
                        kind,
                        service,
                        board.mode,
                        max_calling_points=config.announcements.max_calling_points,
                        station=named,
                    )
                    self._enqueue(
                        Announcement(
                            text,
                            kind,
                            service,
                            board.mode,
                            config.announcements.max_calling_points,
                            named,
                            board.crs if named else None,
                        )
                    )

        self._prune(now)

    def _enqueue(self, announcement: Announcement) -> None:
        try:
            self._queue.put_nowait(announcement)
            log.info("Queued announcement: %s", announcement.text[:80])
        except asyncio.QueueFull:
            log.warning("Announcement queue full; dropping: %s", announcement.text[:60])

    def _prune(self, now: datetime) -> None:
        """Forget services no board has shown for a while.

        Pruned on age rather than on absence: a station can leave the board and
        come back within the day, and its trains must not be announced twice.
        """
        self._announced = {
            key: seen for key, seen in self._announced.items() if now - seen < FORGET_AFTER
        }

    # -- speaking ----------------------------------------------------------

    def _clips(self, announcement: Announcement) -> list[Part] | None:
        """The announcement as recorded clips, or None when Piper should speak it."""
        recorded = getattr(self._engine, "recorded", None)
        if recorded is None:
            return None
        if announcement.kind is None or announcement.service is None:
            pack = recorded.pack()
            if pack is not None and announcement.text == TEST_TEXT and pack.has("p.test"):
                return ["p.test"]
            return None
        plan = recorded.plan(
            announcement.kind,
            announcement.service,
            announcement.mode,
            announcement.max_calling_points,
            announcement.station,
            announcement.station_crs,
        )
        return plan.parts if plan else None

    async def say(self, what: str | Announcement, audio_device: str | None = None) -> None:
        """Speak immediately (used by the test buttons on /admin and /setup).

        The recorded voice speaks when it has every clip the announcement
        needs; otherwise, or if it fails, Piper does.
        """
        announcement = what if isinstance(what, Announcement) else Announcement(what)
        parts = self._clips(announcement)
        spoken = False
        if parts:
            try:
                await self._engine.speak_clips(parts, audio_device)
                spoken = True
            except TtsError as exc:
                log.warning("%s; Piper speaks this one", exc)
        if not spoken:
            await self._engine.speak(announcement.text, audio_device)
        self.last_spoken = announcement.text
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
