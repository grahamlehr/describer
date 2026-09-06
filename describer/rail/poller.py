"""Background polling of every configured station.

Owns the only copy of the live boards. On failure it keeps serving the last
good board with ``stale`` set, and backs off exponentially before retrying.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from ..config import Config, ConfigStore
from ..schedule import is_display_on, set_display_mode, set_display_power
from .base import RailApiError
from .models import Board
from .sources import SourceManager

log = logging.getLogger(__name__)

#: Backoff multipliers applied to the poll interval after consecutive failures.
BACKOFF_STEPS: tuple[float, ...] = (1, 2, 4, 8, 16, 30)

BoardsListener = Callable[[list[Board], Config], Awaitable[None]]


def _slot_key(index: int, crs: str, mode: str) -> str:
    """Stable identity for a station slot; a station may appear twice."""
    return f"{index}:{crs}:{mode}"


class Poller:
    def __init__(self, store: ConfigStore) -> None:
        self._store = store
        self._boards: dict[str, Board] = {}
        self._failures: dict[str, int] = {}
        self._next_due: dict[str, datetime] = {}
        self._sources = SourceManager(store.get().sources)
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._listeners: list[BoardsListener] = []
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._display_on = True
        #: The resolution wlr-randr last accepted; None until a request is made.
        self._display_mode: str | None = None
        self.last_error: str | None = None
        self.last_fetch: datetime | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="describer-poller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._sources.aclose()

    def add_listener(self, listener: BoardsListener) -> None:
        self._listeners.append(listener)

    # -- sources -----------------------------------------------------------

    @property
    def sources(self) -> SourceManager:
        return self._sources

    def force_source(self, source: str | None) -> None:
        """Pin the live source for testing; re-poll straight away."""
        self._sources.force(source)
        self.config_changed()

    def config_changed(self) -> None:
        """Called by the admin page: re-poll immediately under the new config."""
        self._next_due.clear()
        self._wake.set()

    # -- subscribers -------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=4)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(queue)

    def _publish(self, state: dict) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                # A wedged client must not stall the poller; drop its oldest frame.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            try:
                queue.put_nowait(state)
            except asyncio.QueueFull:
                log.debug("Dropped a frame for a slow subscriber")

    # -- state -------------------------------------------------------------

    def boards(self) -> list[Board]:
        config = self._store.get()
        result: list[Board] = []
        for index, station in enumerate(config.stations):
            key = _slot_key(index, station.crs, station.mode)
            board = self._boards.get(key)
            if board is None:
                board = Board(
                    crs=station.crs,
                    name=station.name or station.crs,
                    mode=station.mode,
                    stale=True,
                    error="Waiting for first fetch",
                )
            elif station.name:
                board = board.model_copy(update={"name": station.name})
            result.append(board)
        return result

    @property
    def display_mode(self) -> str | None:
        """The resolution in force, or None while a change is still pending."""
        wanted = self._store.get().display.resolution
        return wanted if wanted == self._display_mode else None

    def state(self) -> dict:
        config = self._store.get()
        return {
            "type": "state",
            "server_time": datetime.now().astimezone().isoformat(),
            "display_on": self._display_on,
            "display": config.display.model_dump(mode="json"),
            "stations": [station.model_dump(mode="json") for station in config.stations],
            "boards": [board.model_dump(mode="json") for board in self.boards()],
        }

    # -- polling -----------------------------------------------------------

    def _mark_stale(self, key: str, config: Config, reason: str) -> None:
        board = self._boards.get(key)
        if board is None:
            return
        age = None
        if board.fetched_at is not None:
            age = (datetime.now().astimezone() - board.fetched_at).total_seconds()
        stale = age is None or age > config.sources.stale_after
        self._boards[key] = board.model_copy(
            update={"stale": stale, "error": reason if stale else None}
        )

    async def _poll_slot(self, index: int, config: Config) -> None:
        station = config.stations[index]
        key = _slot_key(index, station.crs, station.mode)
        self._sources.update_config(config.sources)
        try:
            board = await self._sources.fetch_board(station.crs, station.mode)
        except RailApiError as exc:
            self._failures[key] = self._failures.get(key, 0) + 1
            self.last_error = str(exc)
            self._mark_stale(key, config, str(exc))
            step = BACKOFF_STEPS[min(self._failures[key] - 1, len(BACKOFF_STEPS) - 1)]
            # Jitter keeps two stations from retrying in lockstep.
            interval = self._sources.poll_interval(config.sources.poll_interval)
            delay = interval * step * (1 + random.uniform(0, 0.1))
            self._next_due[key] = datetime.now() + timedelta(seconds=delay)
            log.warning("Poll failed for %s (%s); retrying in %.0fs", station.crs, exc, delay)
            return

        if station.name:
            board = board.model_copy(update={"name": station.name})
        self._boards[key] = board
        self._failures[key] = 0
        self.last_error = None
        self.last_fetch = board.fetched_at
        self._next_due[key] = datetime.now() + timedelta(
            # A metered source (RTT's free tier) is allowed to slow us down.
            seconds=self._sources.poll_interval(config.sources.poll_interval)
        )

    async def _notify_listeners(self, boards: list[Board], config: Config) -> None:
        for listener in self._listeners:
            try:
                await listener(boards, config)
            except Exception:  # a broken listener must not stop the poller
                log.exception("Board listener failed")

    async def _run(self) -> None:
        log.info("Poller started")
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Unexpected poller error")
                await asyncio.sleep(5)

    async def _tick(self) -> None:
        config = self._store.get()
        now = datetime.now()

        display_on = is_display_on(config.schedule, now)
        if display_on != self._display_on:
            self._display_on = display_on
            log.info("Display schedule: %s", "on" if display_on else "off")
            await set_display_power(config.schedule, display_on)
            self._publish(self.state())

        if not display_on:
            await self._sleep_until(now + timedelta(seconds=30))
            return

        await self._apply_display_mode(config)

        due = [
            index
            for index, station in enumerate(config.stations)
            if self._next_due.get(_slot_key(index, station.crs, station.mode), now) <= now
        ]
        if due:
            for index in due:
                await self._poll_slot(index, config)
            boards = self.boards()
            self._publish(self.state())
            await self._notify_listeners(boards, config)

        upcoming = [
            self._next_due.get(_slot_key(index, station.crs, station.mode), now)
            for index, station in enumerate(config.stations)
        ]
        fallback_interval = self._sources.poll_interval(config.sources.poll_interval)
        target = min(upcoming) if upcoming else now + timedelta(seconds=fallback_interval)
        await self._sleep_until(target)

    async def _apply_display_mode(self, config: Config) -> None:
        """Push the configured resolution to the compositor, once, when it changes.

        A fresh process leaves ``auto`` alone: cage already starts the output in
        its preferred mode, and the kiosk may not even be up yet. A failed
        request is retried on the next tick, so a change made before cage
        started still lands.
        """
        wanted = config.display.resolution
        if wanted == self._display_mode:
            return
        if wanted == "auto" and self._display_mode is None:
            self._display_mode = wanted
            return
        if await set_display_mode(config.display):
            self._display_mode = wanted

    async def _sleep_until(self, target: datetime) -> None:
        """Sleep, but wake early if the config changes."""
        delay = max((target - datetime.now()).total_seconds(), 0.5)
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        except TimeoutError:
            return
        self._wake.clear()
