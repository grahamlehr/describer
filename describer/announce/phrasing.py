"""Turns a :class:`Service` into classic station-announcement wording."""

from __future__ import annotations

from enum import StrEnum

from ..rail.models import Service

_UNITS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty")


class AnnouncementKind(StrEnum):
    ARRIVING = "arriving"
    DELAYED = "delayed"
    CANCELLED = "cancelled"


def spell_number(value: int) -> str:
    """0-59 as words, so Piper never guesses at a bare numeral."""
    if value < 20:
        return _UNITS[value]
    tens, units = divmod(value, 10)
    return _TENS[tens] if not units else f"{_TENS[tens]} {_UNITS[units]}"


def speak_time(hhmm: str | None) -> str:
    """ "14:32" -> "fourteen thirty two"; "14:00" -> "fourteen hundred hours"."""
    if not hhmm or ":" not in hhmm:
        return ""
    try:
        hours, minutes = (int(part) for part in hhmm.split(":", 1))
    except ValueError:
        return hhmm
    hour_words = spell_number(hours)
    if minutes == 0:
        return f"{hour_words} hundred hours"
    if minutes < 10:
        return f"{hour_words} oh {spell_number(minutes)}"
    return f"{hour_words} {spell_number(minutes)}"


def join_places(names: list[str]) -> str:
    """ "A, B and C" — the way a station announcer reads a calling list."""
    names = [name for name in names if name]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def calling_points_phrase(service: Service, limit: int = 8) -> str:
    """Calling points, trimmed to ``limit`` while keeping the final stop."""
    names = [point.name for point in service.calling_points if not point.cancelled]
    if not names:
        return ""
    if len(names) > limit:
        names = names[: limit - 1] + [names[-1]]
    return join_places(names)


def _service_phrase(service: Service, mode: str) -> str:
    """ "the 14:32 Great Western Railway service to London Paddington"."""
    time_words = speak_time(service.scheduled_time)
    where = service.origin if mode == "arrivals" else service.destination
    preposition = "from" if mode == "arrivals" else "to"
    parts = ["the", time_words]
    if service.operator:
        parts.append(service.operator)
    parts.append("service")
    if where:
        parts += [preposition, where]
    return " ".join(part for part in parts if part)


def _platform_phrase(service: Service) -> str:
    if not service.platform:
        return ""
    platform = service.platform.strip()
    spoken = spell_number(int(platform)) if platform.isdigit() else platform
    return f"platform {spoken}"


def arriving_text(
    service: Service, mode: str = "departures", *, max_calling_points: int = 8
) -> str:
    """The standard "next train to arrive" announcement."""
    platform = _platform_phrase(service)
    where = "at " + platform if platform else "at this station"
    sentence = f"The next train to arrive {where} will be {_service_phrase(service, mode)}"

    if mode == "departures":
        calling = calling_points_phrase(service, max_calling_points)
        if calling:
            sentence += f", calling at {calling}"
    sentence += "."

    if service.status.value == "expected" and service.delay_minutes:
        sentence += (
            f" This train is running approximately {spell_number(service.delay_minutes)} "
            f"{'minute' if service.delay_minutes == 1 else 'minutes'} late."
        )
    return sentence


def delayed_text(service: Service, mode: str = "departures") -> str:
    """The standard apology for a delay."""
    sentence = f"We are sorry to announce that {_service_phrase(service, mode)} is delayed"
    if service.delay_minutes:
        unit = "minute" if service.delay_minutes == 1 else "minutes"
        sentence += f" by approximately {spell_number(service.delay_minutes)} {unit}"
    sentence += "."
    if service.delay_reason:
        sentence += f" This is due to {_reason(service.delay_reason)}."
    sentence += " We apologise for the delay to your journey."
    return sentence


def cancelled_text(service: Service, mode: str = "departures") -> str:
    """The standard apology for a cancellation."""
    sentence = f"We are sorry to announce that {_service_phrase(service, mode)} has been cancelled."
    if service.cancel_reason:
        sentence += f" This is due to {_reason(service.cancel_reason)}."
    sentence += " We apologise for the inconvenience this will cause to your journey."
    return sentence


def _reason(raw: str) -> str:
    """Darwin reasons already read as prose; just tidy the edges."""
    reason = raw.strip().rstrip(".")
    lowered = reason.lower()
    for prefix in ("this is due to ", "due to "):
        if lowered.startswith(prefix):
            reason = reason[len(prefix) :]
            break
    return reason[0].lower() + reason[1:] if reason else reason


def build_text(
    kind: AnnouncementKind,
    service: Service,
    mode: str = "departures",
    *,
    max_calling_points: int = 8,
) -> str:
    match kind:
        case AnnouncementKind.ARRIVING:
            return arriving_text(service, mode, max_calling_points=max_calling_points)
        case AnnouncementKind.DELAYED:
            return delayed_text(service, mode)
        case AnnouncementKind.CANCELLED:
            return cancelled_text(service, mode)
