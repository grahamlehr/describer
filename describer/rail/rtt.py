"""Realtime Trains client for the next-generation API — the fallback source.

RTT is a genuinely separate upstream: different operator, different
credentials, no shared infrastructure with the Rail Data Marketplace.

Authentication is a bearer token from https://api-portal.rtt.io. A token is
either a long-life *access* token, used as-is, or a long-life *refresh* token
that buys a short-life access token from ``/api/get_access_token``. We are not
told which we hold, so the client tries the exchange once and remembers what
worked.

One board is one ``/rtt/location`` call. That endpoint returns every service
touching the station in a time window, each carrying an ``arrival`` block, a
``departure`` block, or both — so departures and arrivals are two readings of
one response rather than two endpoints. Calling points still need a call per
service, so only the rows that can reach the screen get one.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

import httpx

from .base import RailApiError
from .models import Board, CallingPoint, Service, ServiceStatus

log = logging.getLogger(__name__)

#: This client's identity in config, logs and ``Board.source``.
SOURCE_NAME = "rtt"

#: The only namespace this board cares about; RTT can serve others.
NAMESPACE = "gb-nr"

#: Calling points barely move, and a detail call is a whole round trip.
DETAIL_TTL = 600.0

#: Renew an access token this long before it expires.
TOKEN_MARGIN = 60.0

#: Below this many calls left in a period, skip the optional detail calls.
#: Two stations polling on the floor spend ~60 calls an hour on boards alone,
#: so the hourly figure is the one that usually bites.
DETAIL_BUDGET = {"minute": 3, "hour": 25}

#: Locations a passenger cannot use, and services that are not really trains.
_UNUSABLE_DISPLAY = {"PASS", "DIVERTED"}
#: A set-down-only call cannot be boarded; a pick-up-only call cannot be left.
_NO_BOARDING = "ADVERTISED_SET_DOWN"
_NO_ALIGHTING = "ADVERTISED_PICK_UP"


def token() -> str:
    """The RTT bearer token from the environment. Never logged, never persisted."""
    value = os.environ.get("RTT_TOKEN", "").strip()
    if not value:
        raise RailApiError("RTT_TOKEN is not set")
    return value


def has_credentials() -> bool:
    """True when the environment can authenticate against RTT."""
    return bool(os.environ.get("RTT_TOKEN", "").strip())


def _hhmm(value: Any) -> str | None:
    """RTT sends ISO datetimes; the board speaks "HH:MM"."""
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        return text.split("T", 1)[1][:5]
    return text[:5] or None


def _lateness(block: dict[str, Any], scheduled: str | None, realtime: str | None) -> int:
    """Minutes late: RTT's own figure where it has one, else the clock difference."""
    reported = block.get("realtimeAdvertisedLateness")
    if reported is None:
        reported = block.get("realtimeInternalLateness")
    if isinstance(reported, int):
        return max(reported, 0)
    if not (scheduled and realtime):
        return 0
    sched_h, sched_m = (int(p) for p in scheduled.split(":"))
    real_h, real_m = (int(p) for p in realtime.split(":"))
    delta = (real_h * 60 + real_m) - (sched_h * 60 + sched_m)
    if delta < -720:  # forecast rolled past midnight
        delta += 1440
    return max(delta, 0)


def _realtime(block: dict[str, Any]) -> str | None:
    """What actually happened, else what is forecast, else nothing."""
    for key in ("realtimeActual", "realtimeForecast", "realtimeEstimate"):
        value = _hhmm(block.get(key))
        if value:
            return value
    return None


def _status(realtime: str | None, cancelled: bool, delay: int) -> ServiceStatus:
    """Without an estimate RTT knows nothing; it has no bare "Delayed" state."""
    if cancelled:
        return ServiceStatus.CANCELLED
    if realtime is None:
        return ServiceStatus.UNKNOWN
    return ServiceStatus.EXPECTED if delay else ServiceStatus.ON_TIME


def _joined(entries: Any) -> str:
    """The ``description``s of an origin/destination list, joined."""
    if not isinstance(entries, list):
        return ""
    names = []
    for entry in entries:
        if isinstance(entry, dict):
            location = entry.get("location")
            if isinstance(location, dict):
                names.append(str(location.get("description") or "").strip())
    return " and ".join(name for name in names if name)


def _platform(metadata: dict[str, Any]) -> str | None:
    """Only a confirmed platform, for parity with LDBWS, which withholds the rest."""
    platform = metadata.get("platform")
    if not isinstance(platform, dict):
        return None
    for key in ("actual", "forecast"):
        value = platform.get(key)
        if value:
            return str(value)
    return None


def _reasons(entries: Any) -> tuple[str | None, str | None]:
    """(cancel_reason, delay_reason) — v1 had neither; this API has both."""
    cancel = delay = None
    if not isinstance(entries, list):
        return cancel, delay
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = entry.get("longText") or entry.get("shortText")
        if not text:
            continue
        if entry.get("type") == "CANCEL" and cancel is None:
            cancel = str(text)
        elif entry.get("type") == "DELAY" and delay is None:
            delay = str(text)
    return cancel, delay


def _usable(temporal: dict[str, Any], mode: str) -> dict[str, Any] | None:
    """The arrival or departure block this board reads, when the stop is one we show."""
    if str(temporal.get("displayAs") or "PASS") in _UNUSABLE_DISPLAY:
        return None
    call_type = str(temporal.get("realtimeCallType") or temporal.get("scheduledCallType") or "")
    if mode == "departures":
        # A train that only sets passengers down here is not a departure they can take.
        if call_type == _NO_BOARDING:
            return None
        block = temporal.get("departure")
    else:
        if call_type == _NO_ALIGHTING:
            return None
        block = temporal.get("arrival")
    return block if isinstance(block, dict) else None


def _wanted(metadata: dict[str, Any], include_buses: bool) -> bool:
    if metadata.get("inPassengerService") is False:
        return False
    return include_buses or str(metadata.get("modeType") or "TRAIN") == "TRAIN"


def parse_board(
    payload: dict[str, Any], crs: str, mode: str, *, include_buses: bool = False
) -> Board:
    """Turn one ``/rtt/location`` response into a :class:`Board`.

    Calling points are left empty; :meth:`RttClient.fetch_board` fills them in
    from the per-service detail endpoint.
    """
    services: list[Service] = []

    for index, raw in enumerate(payload.get("services") or []):
        if not isinstance(raw, dict):
            continue
        metadata = raw.get("scheduleMetadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if not _wanted(metadata, include_buses):
            continue

        temporal = raw.get("temporalData")
        temporal = temporal if isinstance(temporal, dict) else {}
        block = _usable(temporal, mode)
        if block is None:
            continue

        scheduled = _hhmm(block.get("scheduleAdvertised") or block.get("scheduleInternal"))
        realtime = _realtime(block)
        cancelled = bool(block.get("isCancelled")) or temporal.get("displayAs") == "CANCELLED"
        delay = _lateness(block, scheduled, realtime)
        cancel_reason, delay_reason = _reasons(raw.get("reasons"))
        location_metadata = raw.get("locationMetadata")
        location_metadata = location_metadata if isinstance(location_metadata, dict) else {}
        identity = str(metadata.get("uniqueIdentity") or f"{crs}-{mode}-{index}")

        services.append(
            Service(
                id=f"rtt:{identity}",
                scheduled_time=scheduled,
                # Mirror LDBWS: the estimate column carries a time or a word.
                expected_time="Cancelled" if cancelled else realtime,
                destination=_joined(raw.get("destination")),
                origin=_joined(raw.get("origin")),
                operator=str((metadata.get("operator") or {}).get("name") or ""),
                operator_code=str((metadata.get("operator") or {}).get("code") or ""),
                platform=_platform(location_metadata),
                status=_status(realtime, cancelled, delay),
                delay_minutes=0 if cancelled else delay,
                cancel_reason=cancel_reason,
                delay_reason=delay_reason,
                length=location_metadata.get("numberOfVehicles"),
            )
        )

    services.sort(key=lambda service: service.scheduled_time or "")

    query = payload.get("query")
    location = (query or {}).get("location") if isinstance(query, dict) else None
    location = location if isinstance(location, dict) else {}
    short_codes = location.get("shortCodes")
    return Board(
        crs=str((short_codes or [crs])[0]).upper(),
        name=str(location.get("description") or crs),
        mode=mode,
        fetched_at=datetime.now().astimezone(),
        services=services,
    )


def parse_calling_points(payload: dict[str, Any], crs: str, mode: str) -> list[CallingPoint]:
    """Calling points from a ``/rtt/service`` response, relative to ``crs``.

    Departures list the stops after this station, arrivals the stops before
    it; both stay in chronological order, as LDBWS gives them.
    """
    service = payload.get("service")
    service = service if isinstance(service, dict) else payload
    stops = [loc for loc in (service.get("locations") or []) if isinstance(loc, dict)]

    def codes(stop: dict[str, Any]) -> set[str]:
        location = stop.get("location")
        location = location if isinstance(location, dict) else {}
        return {str(code).upper() for code in (location.get("shortCodes") or [])}

    here = next((i for i, stop in enumerate(stops) if crs.upper() in codes(stop)), None)
    if here is None:
        return []

    points: list[CallingPoint] = []
    for stop in stops[here + 1 :] if mode == "departures" else stops[:here]:
        temporal = stop.get("temporalData")
        temporal = temporal if isinstance(temporal, dict) else {}
        if str(temporal.get("displayAs") or "PASS") in _UNUSABLE_DISPLAY:
            continue
        # On the way out a stop is timed by its arrival; on the way in, its departure.
        order = ("arrival", "departure") if mode == "departures" else ("departure", "arrival")
        block = next(
            (temporal[key] for key in order if isinstance(temporal.get(key), dict)),
            {},
        )
        location = stop.get("location")
        location = location if isinstance(location, dict) else {}
        short_codes = location.get("shortCodes") or []
        points.append(
            CallingPoint(
                name=str(location.get("description") or ""),
                crs=str(short_codes[0]) if short_codes else None,
                scheduled_time=_hhmm(
                    block.get("scheduleAdvertised") or block.get("scheduleInternal")
                ),
                expected_time=_realtime(block),
                cancelled=bool(block.get("isCancelled"))
                or temporal.get("displayAs") == "CANCELLED",
            )
        )
    return points


class RttClient:
    """Async Realtime Trains client. One instance is shared by the poller."""

    name = SOURCE_NAME

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        include_buses: bool = False,
        detail_rows: int = 3,
        time_window: int = 60,
        min_poll_interval: int = 120,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._include_buses = include_buses
        self._detail_rows = detail_rows
        self._time_window = time_window
        #: RTT's free tier is metered per minute and per hour, so the poller is
        #: not allowed to run faster than this while RTT is the live source.
        self.min_poll_interval = min_poll_interval
        #: Whatever the last response said about our remaining allowance.
        self.rate_limit: dict[str, int] = {}
        #: Set while we are economising, so the reason is logged only once.
        self._detail_skipping = False
        self._details: dict[str, tuple[float, dict[str, Any]]] = {}
        #: Cached access token and the moment it stops being usable.
        self._access: tuple[str, float] | None = None
        #: True once the exchange has told us we hold an access token already.
        self._token_is_access = False
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "describer/1.0"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- authentication ----------------------------------------------------

    async def _bearer(self, *, renew: bool = False) -> str:
        """A usable bearer, exchanging the configured token when it is a refresh one."""
        configured = token()
        if self._token_is_access:
            return configured

        now = asyncio.get_running_loop().time()
        if not renew and self._access is not None and self._access[1] > now:
            return self._access[0]

        response = await self._request(
            "/api/get_access_token", bearer=configured, what="access token"
        )
        if response is None:
            # The exchange refused us: we are holding an access token already.
            self._token_is_access = True
            return configured

        access = str(response.get("token") or "")
        if not access:
            raise RailApiError("Access token exchange returned no token")
        expires = now + self._seconds_until(response.get("validUntil"))
        self._access = (access, expires)
        return access

    @staticmethod
    def _seconds_until(value: Any) -> float:
        """Seconds an access token remains usable, with a margin. Short if unknown."""
        try:
            expiry = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return 300.0
        remaining = (expiry - datetime.now(tz=expiry.tzinfo)).total_seconds()
        return max(remaining - TOKEN_MARGIN, 30.0)

    # -- HTTP --------------------------------------------------------------

    async def _request(
        self, path: str, *, bearer: str, what: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """One GET. Returns None on 401, which the caller reads as "not this token"."""
        try:
            response = await self._client.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {bearer}"},
            )
            self._note_rate_limit(response.headers)
            if response.status_code == 401:
                return None
            if response.status_code == 204:
                return {}
            if response.status_code == 429:
                retry = response.headers.get("Retry-After", "?")
                raise RailApiError(f"Rate limited for {what}; retry after {retry}s")
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # Status only — the body can echo our token back at us.
            raise RailApiError(f"HTTP {exc.response.status_code} for {what}") from exc
        except httpx.HTTPError as exc:
            raise RailApiError(f"{type(exc).__name__} for {what}") from exc
        except ValueError as exc:
            raise RailApiError(f"Malformed JSON for {what}") from exc

        if not isinstance(payload, dict):
            raise RailApiError(f"Unexpected payload type for {what}")
        return payload

    def _note_rate_limit(self, headers: Any) -> None:
        """Remember our remaining allowance; the admin page shows it."""
        for dimension in ("minute", "hour", "day"):
            value = headers.get(f"X-RateLimit-Remaining-{dimension.capitalize()}")
            if value is None:
                continue
            try:
                self.rate_limit[dimension] = int(value)
            except ValueError:
                continue
        remaining_hour = self.rate_limit.get("hour")
        if remaining_hour is not None and remaining_hour <= 10:
            log.warning("RTT allowance nearly spent: %s calls left this hour", remaining_hour)

    async def _get(self, path: str, what: str, params: dict[str, Any] | None = None) -> dict:
        """A data call, renewing the access token once if it has gone stale."""
        payload = await self._request(path, bearer=await self._bearer(), what=what, params=params)
        if payload is None:
            payload = await self._request(
                path, bearer=await self._bearer(renew=True), what=what, params=params
            )
        if payload is None:
            raise RailApiError(f"Not authorised for {what}")
        return payload

    # -- boards ------------------------------------------------------------

    async def _detail(self, identity: str) -> dict[str, Any]:
        now = asyncio.get_running_loop().time()
        cached = self._details.get(identity)
        if cached is not None and cached[0] > now:
            return cached[1]
        payload = await self._get(
            "/rtt/service", f"service {identity}", {"uniqueIdentity": identity}
        )
        self._details[identity] = (now + DETAIL_TTL, payload)
        return payload

    def _prune_details(self, keep: set[str]) -> None:
        """Yesterday's trains must not accumulate in a process that never restarts."""
        now = asyncio.get_running_loop().time()
        self._details = {
            key: value for key, value in self._details.items() if key in keep or value[0] > now
        }

    def _can_afford_details(self) -> bool:
        """Whether the allowance can spare a round trip for calling points."""
        for period, floor in DETAIL_BUDGET.items():
            remaining = self.rate_limit.get(period)
            if remaining is not None and remaining <= floor:
                if not self._detail_skipping:
                    log.info(
                        "Skipping RTT calling points: %s calls left this %s",
                        remaining,
                        period,
                    )
                self._detail_skipping = True
                return False
        self._detail_skipping = False
        return True

    async def fetch_board(self, crs: str, mode: str = "departures") -> Board:
        """Fetch one station board. Raises :class:`RailApiError` on failure."""
        payload = await self._get(
            "/rtt/location",
            crs,
            {"code": f"{NAMESPACE}:{crs.upper()}", "timeWindow": self._time_window},
        )
        board = parse_board(payload, crs, mode, include_buses=self._include_buses)

        # Only the rows that can reach the screen are worth a round trip, and
        # never at the cost of the next board: calling points are decoration,
        # a board is not.
        wanted: set[str] = set()
        rows = board.services[: self._detail_rows] if self._can_afford_details() else []
        for service in rows:
            identity = service.id.removeprefix("rtt:")
            wanted.add(identity)
            try:
                detail = await self._detail(identity)
            except RailApiError as exc:
                # A board without calling points still beats no board at all.
                log.debug("No calling points for %s: %s", identity, exc)
                continue
            service.calling_points = parse_calling_points(detail, board.crs, mode)

        self._prune_details(wanted)
        return board
