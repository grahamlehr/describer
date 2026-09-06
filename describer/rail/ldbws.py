"""Rail Data Marketplace LDBWS (Darwin) JSON client.

The parsing half is deliberately pure so it can be exercised against recorded
responses in ``tests/fixtures`` without touching the network. The HTTP half
implements :class:`~describer.rail.base.RailSource`.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from html import unescape
from typing import Any

import httpx

from .base import HHMM_RE as _HHMM_RE
from .base import RailApiError
from .base import delay_minutes as _delay_minutes
from .models import Board, CallingPoint, Service, ServiceStatus

log = logging.getLogger(__name__)

#: This client's identity in config, logs and ``Board.source``.
SOURCE_NAME = "rdm"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def api_key() -> str:
    """The RDM API key from the environment. Never logged, never persisted."""
    key = os.environ.get("RDM_API_KEY", "").strip()
    if not key:
        raise RailApiError("RDM_API_KEY is not set")
    return key


def has_credentials() -> bool:
    """True when the environment can authenticate against RDM."""
    return bool(os.environ.get("RDM_API_KEY", "").strip())


def _strip_html(value: str) -> str:
    return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", value))).strip()


def _first_location(entries: Any) -> dict[str, Any]:
    if isinstance(entries, list) and entries:
        first = entries[0]
        if isinstance(first, dict):
            return first
    if isinstance(entries, dict):
        return entries
    return {}


def _location_name(entries: Any) -> str:
    location = _first_location(entries)
    name = str(location.get("locationName") or "")
    via = location.get("via")
    if via:
        name = f"{name} {str(via).strip()}"
    return name.strip()


def _status(raw: str | None, cancelled: bool, delay: int) -> ServiceStatus:
    text = (raw or "").strip()
    lowered = text.lower()
    if cancelled or lowered == "cancelled":
        return ServiceStatus.CANCELLED
    if lowered == "on time":
        return ServiceStatus.ON_TIME
    if lowered == "delayed":
        return ServiceStatus.DELAYED
    if _HHMM_RE.match(text):
        return ServiceStatus.EXPECTED if delay else ServiceStatus.ON_TIME
    return ServiceStatus.UNKNOWN


def _calling_points(service: dict[str, Any], mode: str) -> list[CallingPoint]:
    key = "subsequentCallingPoints" if mode == "departures" else "previousCallingPoints"
    groups = service.get(key) or []
    points: list[CallingPoint] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for point in group.get("callingPoint") or []:
            if not isinstance(point, dict):
                continue
            expected = point.get("et")
            points.append(
                CallingPoint(
                    name=str(point.get("locationName") or ""),
                    crs=point.get("crs"),
                    scheduled_time=point.get("st"),
                    expected_time=expected,
                    cancelled=str(expected or "").lower() == "cancelled",
                )
            )
    return points


def parse_board(payload: dict[str, Any], crs: str, mode: str) -> Board:
    """Turn one LDBWS response into a :class:`Board`."""
    time_key, est_key = ("std", "etd") if mode == "departures" else ("sta", "eta")
    services: list[Service] = []

    for index, raw in enumerate(payload.get("trainServices") or []):
        if not isinstance(raw, dict):
            continue
        scheduled = raw.get(time_key)
        expected = raw.get(est_key)
        delay = _delay_minutes(scheduled, expected)
        status = _status(expected, bool(raw.get("isCancelled")), delay)
        services.append(
            Service(
                id=str(raw.get("serviceID") or f"{crs}-{mode}-{index}"),
                scheduled_time=scheduled,
                expected_time=expected,
                destination=_location_name(raw.get("destination")),
                origin=_location_name(raw.get("origin")),
                operator=str(raw.get("operator") or ""),
                operator_code=str(raw.get("operatorCode") or ""),
                platform=raw.get("platform"),
                status=status,
                delay_minutes=delay if status is not ServiceStatus.CANCELLED else 0,
                cancel_reason=raw.get("cancelReason"),
                delay_reason=raw.get("delayReason"),
                calling_points=_calling_points(raw, mode),
                length=raw.get("length"),
            )
        )

    messages: list[str] = []
    for message in payload.get("nrccMessages") or []:
        text = message.get("value") if isinstance(message, dict) else message
        if text:
            messages.append(_strip_html(str(text)))

    generated = payload.get("generatedAt")
    generated_at: datetime | None = None
    if isinstance(generated, str):
        try:
            generated_at = datetime.fromisoformat(generated)
        except ValueError:
            log.debug("Unparseable generatedAt for %s", crs)

    return Board(
        crs=str(payload.get("crs") or crs).upper(),
        name=str(payload.get("locationName") or crs),
        mode=mode,
        generated_at=generated_at,
        fetched_at=datetime.now().astimezone(),
        services=services,
        messages=messages,
    )


class LdbwsClient:
    """Async LDBWS client. One instance is shared by the poller."""

    name = SOURCE_NAME
    #: RDM's fair-use limit is the configured poll interval; nothing extra.
    min_poll_interval = 0
    rate_limit: dict[str, int] = {}

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "describer/1.0"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_board(self, crs: str, mode: str = "departures") -> Board:
        """Fetch one station board. Raises :class:`RailApiError` on failure."""
        endpoint = "GetDepBoardWithDetails" if mode == "departures" else "GetArrBoardWithDetails"
        url = f"{self._base_url}/{endpoint}/{crs.upper()}"
        try:
            response = await self._client.get(url, headers={"x-apikey": api_key()})
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # Status only — the body can echo the key back at us.
            raise RailApiError(f"HTTP {exc.response.status_code} for {crs}") from exc
        except httpx.HTTPError as exc:
            raise RailApiError(f"{type(exc).__name__} for {crs}") from exc
        except ValueError as exc:
            raise RailApiError(f"Malformed JSON for {crs}") from exc

        if not isinstance(payload, dict):
            raise RailApiError(f"Unexpected payload type for {crs}")
        return parse_board(payload, crs, mode)
