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
from ..schedule import is_display_on, set_display_power
from .client import LdbwsClient, RailApiError
from .models import Board

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
        self._client: LdbwsClient | None = None
        self._client_signature: tuple[str, float] | None = None
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._listeners: list[BoardsListener] = []
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._display_on = True
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
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def add_listener(self, listener: BoardsListener) -> None:
        self._listeners.append(listener)

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

    def _get_client(self, config: Config) -> LdbwsClient:
        signature = (config.api.base_url, config.api.timeout)
        if self._client is None or self._client_signature != signature:
            if self._client is not None:
                asyncio.create_task(self._client.aclose())
            self._client = LdbwsClient(config.api.base_url, timeout=config.api.timeout)
            self._client_signature = signature
        return self._client

    def _mark_stale(self, key: str, config: Config, reason: str) -> None:
        board = self._boards.get(key)
        if board is None:
            return
        age = None
        if board.fetched_at is not None:
            age = (datetime.now().astimezone() - board.fetched_at).total_seconds()
        stale = age is None or age > config.api.stale_after
        self._boards[key] = board.model_copy(
            update={"stale": stale, "error": reason if stale else None}
        )

    async def _poll_slot(self, index: int, config: Config) -> None:
        station = config.stations[index]
        key = _slot_key(index, station.crs, station.mode)
        client = self._get_client(config)
        try:
            board = await client.fetch_board(station.crs, station.mode)
        except RailApiError as exc:
            self._failures[key] = self._failures.get(key, 0) + 1
            self.last_error = str(exc)
            self._mark_stale(key, config, str(exc))
            step = BACKOFF_STEPS[min(self._failures[key] - 1, len(BACKOFF_STEPS) - 1)]
            # Jitter keeps two stations from retrying in lockstep.
            delay = config.api.poll_interval * step * (1 + random.uniform(0, 0.1))
            self._next_due[key] = datetime.now() + timedelta(seconds=delay)
            log.warning("Poll failed for %s (%s); retrying in %.0fs", station.crs, exc, delay)
            return

        if station.name:
            board = board.model_copy(update={"name": station.name})
        self._boards[key] = board
        self._failures[key] = 0
        self.last_error = None
        self.last_fetch = board.fetched_at
        self._next_due[key] = datetime.now() + timedelta(seconds=config.api.poll_interval)

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
        target = min(upcoming) if upcoming else now + timedelta(seconds=config.api.poll_interval)
        await self._sleep_until(target)

    async def _sleep_until(self, target: datetime) -> None:
        """Sleep, but wake early if the config changes."""
        delay = max((target - datetime.now()).total_seconds(), 0.5)
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        except TimeoutError:
            return
        self._wake.clear()
