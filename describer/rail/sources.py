"""Picks the live source and fails over between them.

One source is active at a time, globally: both halves of a split screen must
agree about which feed they are showing. The primary is used while it works;
after enough consecutive failures the fallback takes over and the primary is
retried quietly in the background until it comes back.

Failover is not a way to hide a broken board — if the source that is live now
fails, the caller still gets an error and the board goes stale as it always did.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..config import SourcesConfig
from .base import RailApiError, RailSource
from .ldbws import LdbwsClient
from .ldbws import has_credentials as rdm_credentials
from .models import Board
from .rtt import RttClient
from .rtt import has_credentials as rtt_credentials

log = logging.getLogger(__name__)

#: How each source reports whether the environment can authenticate it.
CREDENTIAL_CHECKS = {"rdm": rdm_credentials, "rtt": rtt_credentials}


class SourceManager:
    """Owns one client per source and decides which of them answers."""

    def __init__(self, config: SourcesConfig) -> None:
        self._config = config
        self._clients: dict[str, RailSource] = {}
        self._signatures: dict[str, tuple[Any, ...]] = {}
        self._forced: str | None = None
        self._failures = 0
        self._next_probe: float | None = None
        self._healthy: dict[str, bool] = dict.fromkeys(CREDENTIAL_CHECKS, True)
        self._reported: set[str] = set()
        self._active = config.primary
        self._check_credentials()
        if not self._usable(config.primary) and self._usable(config.fallback):
            self._active = config.fallback  # type: ignore[assignment]
            log.warning("Starting on the fallback source: %s has no credentials", config.primary)

    # -- state -------------------------------------------------------------

    @property
    def active(self) -> str:
        """The source the next fetch will use."""
        return self._forced or self._active

    def force(self, source: str | None) -> None:
        """Pin the active source for testing. In memory only; never saved."""
        if source is not None and source not in CREDENTIAL_CHECKS:
            raise ValueError(f"Unknown source: {source}")
        self._forced = source
        log.info("Source forced to %s", source or "auto")

    def poll_interval(self, configured: int) -> int:
        """The configured interval, unless the live source insists on slower."""
        return max(configured, getattr(self._client(self.active), "min_poll_interval", 0))

    def status(self) -> dict[str, Any]:
        fallback = self._config.fallback
        active_client = self._clients.get(self.active)
        return {
            "active_source": self.active,
            "forced_source": self._forced or "auto",
            "primary": self._config.primary,
            "fallback": fallback,
            "primary_healthy": self._healthy.get(self._config.primary, False),
            "fallback_healthy": self._healthy.get(fallback, False) if fallback else None,
            "credentials": {name: check() for name, check in CREDENTIAL_CHECKS.items()},
            "rate_limit": dict(getattr(active_client, "rate_limit", {}) or {}),
        }

    # -- clients -----------------------------------------------------------

    def _check_credentials(self) -> None:
        """Report a source we can never reach once, not on every tick."""
        for name in (self._config.primary, self._config.fallback):
            if name is None or CREDENTIAL_CHECKS[name]():
                continue
            self._healthy[name] = False
            if name not in self._reported:
                self._reported.add(name)
                log.error("Source %s has no credentials in the environment", name)

    def _usable(self, name: str | None) -> bool:
        return bool(name) and CREDENTIAL_CHECKS[name]()  # type: ignore[index]

    def _client(self, name: str) -> RailSource:
        """The client for one source, rebuilt when its settings change."""
        rdm, rtt = self._config.rdm, self._config.rtt
        signature: tuple[Any, ...] = (
            (rdm.base_url, rdm.timeout)
            if name == "rdm"
            else (
                rtt.base_url,
                rtt.timeout,
                rtt.include_buses,
                rtt.detail_rows,
                rtt.time_window,
                rtt.min_poll_interval,
            )
        )
        if name in self._clients and self._signatures.get(name) == signature:
            return self._clients[name]

        self._close_later(self._clients.pop(name, None))
        if name == "rdm":
            client: RailSource = LdbwsClient(rdm.base_url, timeout=rdm.timeout)
        else:
            client = RttClient(
                rtt.base_url,
                timeout=rtt.timeout,
                include_buses=rtt.include_buses,
                detail_rows=rtt.detail_rows,
                time_window=rtt.time_window,
                min_poll_interval=rtt.min_poll_interval,
            )
        self._clients[name] = client
        self._signatures[name] = signature
        return client

    def _close_later(self, client: RailSource | None) -> None:
        if client is not None:
            asyncio.create_task(client.aclose())

    def update_config(self, config: SourcesConfig) -> None:
        """Adopt a new config from /admin without restarting the poller."""
        roles_changed = (config.primary, config.fallback) != (
            self._config.primary,
            self._config.fallback,
        )
        self._config = config
        if roles_changed:
            self._active = config.primary
            self._failures = 0
            self._next_probe = None
            self._healthy = dict.fromkeys(CREDENTIAL_CHECKS, True)
            self._reported.clear()
            self._check_credentials()
            if not self._usable(config.primary) and self._usable(config.fallback):
                self._active = config.fallback  # type: ignore[assignment]

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        self._signatures.clear()

    # -- fetching ----------------------------------------------------------

    async def _fetch_from(self, name: str, crs: str, mode: str) -> Board:
        if not CREDENTIAL_CHECKS[name]():
            self._healthy[name] = False
            raise RailApiError(f"{name} has no credentials")
        try:
            board = await self._client(name).fetch_board(crs, mode)
        except RailApiError:
            self._healthy[name] = False
            raise
        self._healthy[name] = True
        return board.model_copy(update={"source": name})

    def _probe_due(self) -> bool:
        if self._next_probe is None:
            self._next_probe = time.monotonic() + self._config.recover_after
            return False
        return time.monotonic() >= self._next_probe

    async def _try_recover(self, crs: str, mode: str) -> Board | None:
        """One quiet attempt at the primary while the fallback carries the board."""
        primary = self._config.primary
        if not self._probe_due() or not self._usable(primary):
            return None
        try:
            board = await self._fetch_from(primary, crs, mode)
        except RailApiError as exc:
            self._next_probe = time.monotonic() + self._config.recover_after
            log.debug("Primary source %s still down: %s", primary, exc)
            return None
        self._active = primary
        self._failures = 0
        self._next_probe = None
        log.info("Primary source %s recovered; switching back", primary)
        return board

    async def fetch_board(self, crs: str, mode: str = "departures") -> Board:
        """Fetch one board from the live source. Raises :class:`RailApiError`."""
        if self._forced is not None:
            return await self._fetch_from(self._forced, crs, mode)

        primary, fallback = self._config.primary, self._config.fallback
        if self._active != primary:
            recovered = await self._try_recover(crs, mode)
            if recovered is not None:
                return recovered

        try:
            return await self._fetch_from(self._active, crs, mode)
        except RailApiError as exc:
            if self._active != primary:
                raise  # the fallback is all we have; let the board go stale
            self._failures += 1
            if self._failures < self._config.failover_after or not self._usable(fallback):
                raise
            self._active = fallback  # type: ignore[assignment]
            self._next_probe = None
            log.warning(
                "Source %s failed %d times (%s); failing over to %s",
                primary,
                self._failures,
                exc,
                fallback,
            )
            return await self._fetch_from(self._active, crs, mode)
