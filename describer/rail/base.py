"""The contract every data source implements.

Two upstreams can serve the same board — the Rail Data Marketplace's LDBWS
feed and Realtime Trains — so everything above this module talks to a
:class:`RailSource` and never to a particular API. The small time helpers
live here too, because both parsers must agree on what "seven minutes late"
means.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from .models import Board

#: A board time in the shape every source hands downstream.
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class RailApiError(RuntimeError):
    """Any failure to obtain a usable board from a source."""


@runtime_checkable
class RailSource(Protocol):
    """One upstream feed. The poller holds these, never a concrete client."""

    #: Short identity used in config, logs and ``Board.source``.
    name: str
    #: Seconds this source insists on between polls, 0 when it does not care.
    min_poll_interval: int

    async def fetch_board(self, crs: str, mode: str) -> Board: ...

    async def aclose(self) -> None: ...


def delay_minutes(scheduled: str | None, expected: str | None) -> int:
    """Whole minutes between two "HH:MM" board times, clamped at zero."""
    if not scheduled or not expected:
        return 0
    if not (HHMM_RE.match(scheduled) and HHMM_RE.match(expected)):
        return 0
    sched_h, sched_m = (int(p) for p in scheduled.split(":"))
    exp_h, exp_m = (int(p) for p in expected.split(":"))
    delta = (exp_h * 60 + exp_m) - (sched_h * 60 + sched_m)
    if delta < -720:  # estimate rolled past midnight
        delta += 1440
    return max(delta, 0)
