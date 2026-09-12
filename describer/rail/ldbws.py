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
from .models import Board, CallingPoint, Coach, Formation, Position, Service, ServiceStatus

log = logging.getLogger(__name__)

#: This client's identity in config, logs and ``Board.source``.
SOURCE_NAME = "rdm"

#: The single operation we call. We are subscribed to the combined
#: "Live Arrival and Departure Boards" product, which returns every service
#: touching the station, so departures and arrivals are two readings of one
#: response. The departures-only product exposes GetDepBoardWithDetails
#: instead and cannot serve arrivals at all; changing product means changing
#: this constant and ``sources.rdm.base_url`` together.
BOARD_ENDPOINT = "GetArrDepBoardWithDetails"

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


def _actual_time(point: dict[str, Any]) -> str | None:
    """A previous calling point's ``at``, normalised like any other Darwin estimate."""
    text = str(point.get("at") or "").strip()
    lowered = text.lower()
    if lowered == "on time":
        return point.get("st")
    if not text or lowered in ("delayed", "no report"):
        return None
    return text


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
                    actual_time=_actual_time(point),
                    cancelled=bool(point.get("isCancelled"))
                    or str(expected or "").lower() == "cancelled",
                )
            )
    return points


def _position(raw: dict[str, Any]) -> Position | None:
    """Where the train is, from the stops behind it that Darwin has reported.

    Reads ``previousCallingPoints`` regardless of the board's mode: for an
    arrival that is the list already read by ``_calling_points``, for a
    departure it is a second read of the same response.
    """
    if raw.get("isCancelled"):
        return None
    points: list[dict[str, Any]] = []
    for group in raw.get("previousCallingPoints") or []:
        if not isinstance(group, dict):
            continue
        for point in group.get("callingPoint") or []:
            if isinstance(point, dict) and not point.get("isCancelled"):
                points.append(point)
    if not points:
        return None

    last_reported = -1
    for index, point in enumerate(points):
        if _actual_time(point) is not None:
            last_reported = index

    if last_reported == -1:
        return Position(
            state="not_started",
            next=_location_name(raw.get("origin")) or None,
            stops_away=len(points),
        )

    last_point = points[last_reported]
    ahead = points[last_reported + 1 :]
    if not ahead:
        return Position(
            state="approaching",
            last=str(last_point.get("locationName") or ""),
            last_time=_actual_time(last_point),
        )
    return Position(
        state="between",
        last=str(last_point.get("locationName") or ""),
        last_time=_actual_time(last_point),
        next=str(ahead[0].get("locationName") or ""),
        stops_away=len(ahead),
    )


def _formation(raw: dict[str, Any]) -> Formation | None:
    """The service's coach-by-coach makeup, front-first. None with no formation."""
    formation = raw.get("formation")
    if not isinstance(formation, dict):
        return None
    coaches: list[Coach] = []
    for raw_coach in formation.get("coaches") or []:
        if not isinstance(raw_coach, dict):
            continue
        toilet = raw_coach.get("toilet")
        toilet = toilet if isinstance(toilet, dict) else {}
        coaches.append(
            Coach(
                number=raw_coach.get("number"),
                first_class="first" in str(raw_coach.get("coachClass") or "").lower(),
                accessible_toilet=toilet.get("Value") == "Accessible",
                loading=raw_coach.get("loading") if raw_coach.get("loadingSpecified") else None,
            )
        )
    if not coaches:
        return None
    if raw.get("isReverseFormation"):
        # Darwin lists coaches in whatever order the unit is coupled; this is
        # the only field that says which end is the front.
        coaches.reverse()
    known = [coach.loading for coach in coaches if coach.loading is not None]
    average = round(sum(known) / len(known)) if known else None
    return Formation(coaches=coaches, average_loading=average)


def parse_board(payload: dict[str, Any], crs: str, mode: str) -> Board:
    """Turn one LDBWS response into a :class:`Board`."""
    time_key, est_key = ("std", "etd") if mode == "departures" else ("sta", "eta")
    services: list[Service] = []

    for index, raw in enumerate(payload.get("trainServices") or []):
        if not isinstance(raw, dict):
            continue
        scheduled = raw.get(time_key)
        expected = raw.get(est_key)
        if not scheduled and not expected:
            # The combined board carries arrivals and departures together: a
            # service terminating here has no std, one starting here has no
            # sta. Neither belongs on the board we are reading.
            continue
        delay = _delay_minutes(scheduled, expected)
        status = _status(expected, bool(raw.get("isCancelled")), delay)
        formation = _formation(raw)
        length = raw.get("length")
        if not length and formation is not None:
            length = len(formation.coaches)
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
                length=length,
                position=_position(raw),
                formation=formation,
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
        url = f"{self._base_url}/{BOARD_ENDPOINT}/{crs.upper()}"
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
