"""Normalised rail data.

Everything downstream of the source clients — themes, announcements, the SSE
payload — sees only these types, never a raw LDBWS or RTT response shape.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field


class ServiceStatus(StrEnum):
    ON_TIME = "on_time"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    EXPECTED = "expected"
    UNKNOWN = "unknown"


class CallingPoint(BaseModel):
    name: str
    crs: str | None = None
    scheduled_time: str | None = None
    expected_time: str | None = None
    cancelled: bool = False


class Service(BaseModel):
    """A single train on the board, in either direction."""

    id: str
    #: Scheduled departure (departures mode) or arrival (arrivals mode), "HH:MM".
    scheduled_time: str | None = None
    #: Raw estimate text from Darwin: "On time", "14:37", "Delayed", "Cancelled".
    expected_time: str | None = None
    destination: str = ""
    origin: str = ""
    operator: str = ""
    operator_code: str = ""
    platform: str | None = None
    status: ServiceStatus = ServiceStatus.UNKNOWN
    #: Minutes late, when Darwin gives us a concrete estimate to compare.
    delay_minutes: int = 0
    cancel_reason: str | None = None
    delay_reason: str | None = None
    calling_points: list[CallingPoint] = Field(default_factory=list)
    length: int | None = None

    @property
    def is_cancelled(self) -> bool:
        return self.status is ServiceStatus.CANCELLED

    @property
    def status_text(self) -> str:
        """What the board prints in the status column."""
        match self.status:
            case ServiceStatus.CANCELLED:
                return "Cancelled"
            case ServiceStatus.ON_TIME:
                return "On time"
            case ServiceStatus.DELAYED:
                return "Delayed"
            case ServiceStatus.EXPECTED:
                return f"Exp {self.expected_time}"
            case _:
                return self.expected_time or ""

    def effective_time(self) -> str | None:
        """The time we actually expect, preferring the estimate over the plan."""
        if self.status is ServiceStatus.EXPECTED and self.expected_time:
            return self.expected_time
        return self.scheduled_time

    def seconds_until(self, now: datetime) -> float | None:
        """Seconds from ``now`` until the effective time, or None if unknown.

        Board times carry no date, so a time that has already passed today is
        read as tomorrow only when it is more than an hour behind — that keeps
        a train running a few minutes late from jumping 23 hours into the future.
        """
        hhmm = self.effective_time()
        if not hhmm or ":" not in hhmm:
            return None
        try:
            hours, minutes = (int(part) for part in hhmm.split(":", 1))
            target_time = time(hours, minutes)
        except ValueError:
            return None
        target = datetime.combine(now.date(), target_time, tzinfo=now.tzinfo)
        if target < now - timedelta(hours=1):
            target += timedelta(days=1)
        return (target - now).total_seconds()


class Board(BaseModel):
    """One station's board — half the screen when two stations are configured."""

    crs: str
    name: str
    mode: str = "departures"
    generated_at: datetime | None = None
    fetched_at: datetime | None = None
    services: list[Service] = Field(default_factory=list)
    #: NRCC service messages (disruption notices) as plain text.
    messages: list[str] = Field(default_factory=list)
    #: Which source produced this board: "rdm" or "rtt". Set by the manager.
    source: str = ""
    #: True when the last fetch failed and this is the last good data.
    stale: bool = False
    #: Human-readable reason the board is stale, shown in the indicator.
    error: str | None = None
