"""First-run setup: when the board needs it, and what the /setup page can ask.

A Pi that has just been flashed has no Rail Data Marketplace key, and nobody
to SSH in and type one. The board asks for setup on the TV (a QR code to the
/setup page) and the phone finishes it. This module decides *whether* setup is
needed (``setup_state``), tries a key on request (``check_key``), and folds the
wizard's answers into the config (``apply_setup``).

Only RDM counts. An image with no RTT token is normal, and never needs setup.
"""

from __future__ import annotations

import io
import logging
import socket
from typing import Any, Literal

import segno
from pydantic import BaseModel, Field, field_validator

from .config import Config, CrsCode, RdmSourceConfig, StationConfig
from .rail.base import RailApiError
from .rail.ldbws import LdbwsClient
from .rail.ldbws import has_credentials as rdm_has_credentials

log = logging.getLogger(__name__)

#: The port the backend unit serves on (deploy/describer.service).
PORT = 8080

#: An address reserved for documentation (RFC 5737). Connecting a UDP socket
#: to it makes the kernel choose the outgoing interface, and so the LAN
#: address, without sending a packet.
_ROUTE_PROBE = ("192.0.2.1", 80)


# -- where /setup lives ----------------------------------------------------


def lan_ip() -> str | None:
    """This machine's address on the LAN, or None when it has no route out."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(_ROUTE_PROBE)
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def hostname() -> str:
    """The short hostname, which mDNS answers to as ``<name>.local``."""
    return socket.gethostname().split(".")[0] or "describer"


def network_urls() -> dict[str, str | None]:
    """Where a phone on the same Wi-Fi reaches /setup: by name, and by address."""
    address = lan_ip()
    return {
        "url": f"http://{hostname()}.local:{PORT}/setup",
        "ip_url": f"http://{address}:{PORT}/setup" if address else None,
    }


def qr_svg(text: str) -> bytes:
    """A QR code as SVG: black modules on nothing.

    The page paints it through ``mask-image`` in its own theme colours, so the
    colours are the stylesheet's, and no theme is asked to match a hard-coded
    pair. The quiet zone is the panel behind it, not part of the drawing.
    """
    out = io.BytesIO()
    segno.make(text, error="m").save(
        out, kind="svg", scale=10, border=0, dark="#000", light=None, xmldecl=False
    )
    return out.getvalue()


# -- is setup needed? ------------------------------------------------------


def _fallback_is_serving(poller: Any) -> bool:
    """Is a board on the screen right now that RDM did not supply?

    A source other than RDM carrying fresh data means the board works; putting
    a setup screen over it would hide the very thing the user wants.
    """
    return any(not board.stale and board.source not in ("", "rdm") for board in poller.boards())


def setup_state(poller: Any, store: Any) -> dict[str, Any] | None:
    """Why the board needs setup, with where to do it, or None if it does not.

    ``{"reason": "no_key"}``: RDM is the primary and no key is set.
    ``{"reason": "key_rejected"}``: RDM said 401 or 403 and nothing has served
    a board since. Polling carries on either way; this only changes the screen.
    """
    sources = store.get().sources
    if sources.primary != "rdm" or _fallback_is_serving(poller):
        return None
    if not rdm_has_credentials():
        reason = "no_key"
    elif poller.sources.rdm_auth_failed:
        reason = "key_rejected"
    else:
        return None
    return {"reason": reason, **network_urls()}


# -- trying a key ----------------------------------------------------------


def make_client(base_url: str, timeout: float) -> LdbwsClient:
    """The client the key test uses. A seam for the tests' MockTransport."""
    return LdbwsClient(base_url, timeout=timeout)


async def check_key(key: str, crs: str, mode: str, rdm: RdmSourceConfig) -> dict[str, Any]:
    """One LDBWS request with ``key``, for ``crs``. Never retried, never logged.

    ``ok``          the board, with the station's name and the next train
    ``rejected``    401 or 403: a wrong key, or a key with no subscription to
                    this product (the two cannot be told apart; see below)
    ``unreachable`` anything else: no network, a timeout, a 5xx

    TODO(graham): confirm wording. Nothing in the repo records what RDM answers
    for a valid key that is *not* subscribed to the arrivals-and-departures
    product, so there is no separate ``wrong_product`` outcome: it is folded
    into ``rejected``, whose message on the page names both causes. If a real
    capture shows a distinct status, split it here.
    """
    client = make_client(rdm.base_url, rdm.timeout)
    try:
        board = await client.fetch_board(crs, mode, key=key)
    except RailApiError as exc:
        if exc.is_auth_failure:
            return {"result": "rejected", "status": exc.status}
        # The message is the status or the exception's type, never a body.
        return {"result": "unreachable", "status": exc.status, "detail": str(exc)}
    finally:
        await client.aclose()

    next_train = None
    if board.services:
        service = board.services[0]
        next_train = {
            "time": service.scheduled_time,
            "expected": service.expected_time,
            "destination": service.destination,
            "platform": service.platform,
            "status": service.status.value,
        }
    return {"result": "ok", "station": board.name, "crs": board.crs, "next_train": next_train}


# -- finishing -------------------------------------------------------------


class KeyCheck(BaseModel):
    """``POST /api/setup/test-key``."""

    key: str
    crs: CrsCode
    mode: Literal["departures", "arrivals"] = "departures"


class SetupStation(BaseModel):
    crs: CrsCode
    mode: Literal["departures", "arrivals"] = "departures"


class SetupComplete(BaseModel):
    """``POST /api/setup/complete``. A blank ``key`` keeps the one already saved."""

    stations: list[SetupStation] = Field(min_length=1, max_length=2)
    key: str | None = None
    announce: bool = True
    audio_device: Literal["hdmi", "jack", "default"] = "hdmi"

    @field_validator("stations")
    @classmethod
    def _distinct(cls, stations: list[SetupStation]) -> list[SetupStation]:
        pairs = [(station.crs.upper(), station.mode) for station in stations]
        if len(set(pairs)) != len(pairs):
            raise ValueError("The two stations are the same: pick a different one or mode")
        return stations


def apply_setup(base: Config, answers: SetupComplete) -> Config:
    """The raw config with the wizard's answers in it. Validated, not saved.

    ``base`` must be ``ConfigStore.get()``, never ``active()``: an active
    profile has already been merged into the latter, and saving that would
    write the evening's stations into the base file. Profiles are left exactly
    as they were.

    A station the file already has, in the same mode, keeps its other options
    (rows, platforms, walk time): the page is usable after setup, and a second
    visit must not quietly reset them. A new one takes the defaults.
    """
    known = {(station.crs, station.mode): station for station in base.stations}
    stations = [
        known.get((station.crs.upper(), station.mode))
        or StationConfig(crs=station.crs, mode=station.mode)
        for station in answers.stations
    ]
    data = base.model_dump(mode="json")
    data["stations"] = [station.model_dump(mode="json") for station in stations]
    data["announcements"]["enabled"] = answers.announce
    if answers.announce:
        data["announcements"]["audio_device"] = answers.audio_device
    return Config.model_validate(data)
