"""Every clip a recorded voice needs, and the words each one says.

An announcement in a recorded voice is a string of short clips played end to
end, the way station announcements have always been built. This module is the
list of those clips: a key for each, and the text to record for it. The
recording script (``scripts/voicepack.py``), the playback plan
(``clips.py``) and the tests all read it, so the wording has one home.

Keys:

==============  ==========================================================
``p.<name>``    a fixed phrase ("We are sorry to announce that the")
``n.<0-59>``    a number, said level, mid-sentence: hours, minutes past
                the ten, platforms and minutes late all use it
``m.<00-09>``   the minutes a bare number cannot say: "hundred hours",
                "oh one" to "oh nine"
``l.<a-z>``     a platform's letter ("platform four B")
``o.<slug>``    an operator, by the name the feed gives it
``r.<slug>``    a reason for a delay or cancellation
``s.<slug>.mid``  a station, said as one in a list, more to come
``s.<slug>.end``  the same station, said as the last word of the sentence
==============  ==========================================================

A station is recorded twice because the voice drops at the end of a sentence
and does not in the middle of one; one recording used for both sounds wrong
in one of them.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .phrasing import spell_number

DATA = Path(__file__).parent / "data"
STATIONS = Path(__file__).parent.parent / "web" / "static" / "stations.json"

#: The fixed phrases. A trailing comma asks the voice to carry on rather than
#: finish; a full stop asks it to land. Change a wording here and in
#: ``phrasing.py`` together: ``test_clips.py`` fails if the two disagree.
PHRASES: dict[str, str] = {
    "next_platform": "The next train to arrive at platform,",
    "next_station": "The next train to arrive at this station,",
    "will_be_the": "will be the,",
    "service_to": "service to,",
    "service_from": "service from,",
    "via": "via,",
    "calling_at": "calling at,",
    "and": "and,",
    "running_late": "This train is running approximately,",
    "minute_late": "minute late.",
    "minutes_late": "minutes late.",
    "sorry_the": "We are sorry to announce that the,",
    "is_delayed": "is delayed.",
    "is_delayed_by": "is delayed by approximately,",
    "minute": "minute.",
    "minutes": "minutes.",
    "has_been_cancelled": "has been cancelled.",
    "due_to": "This is due to,",
    "apologise_delay": "We apologise for the delay to your journey.",
    "apologise_inconvenience": (
        "We apologise for the inconvenience this will cause to your journey."
    ),
    "test": "This is a test announcement from the departure board.",
}

#: What Darwin puts in front of a reason. Stripped before the reason is keyed,
#: so "This service has been delayed by a signalling fault" and "This is due
#: to a signalling fault" find the same clip.
_REASON_PREFIX = re.compile(
    r"^(?:this (?:train|service) (?:has been|is being|is|was) "
    r"(?:delayed|cancelled)(?: by| because of| due to)|this is due to|due to|because of)\s+",
    re.IGNORECASE,
)
_PARENTHESES = re.compile(r"\([^)]*\)")
_NOT_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Clip:
    key: str
    #: What to put in the recording script.
    text: str
    #: Which part of the script it belongs to: core, stations, reasons.
    group: str


def slug(text: str) -> str:
    """ "Hayes (Kent)" -> "hayes"; "Highbury & Islington" -> "highbury-and-islington".

    A county in brackets tells two stations apart on paper and is never said
    aloud, so both Bramleys share one clip.
    """
    text = _PARENTHESES.sub(" ", text).replace("&", " and ").replace("'", "").lower()
    return _NOT_WORD.sub("-", text).strip("-")


def spoken_station(name: str) -> str:
    """The station as it should be read: no brackets, "and" for "&"."""
    text = _PARENTHESES.sub(" ", name).replace("&", " and ")
    return " ".join(text.split())


def bare_reason(raw: str) -> str:
    """A reason with Darwin's lead-in and full stop removed."""
    return _REASON_PREFIX.sub("", raw.strip()).rstrip(". ").strip()


def station_key(name: str, inflection: str) -> str:
    return f"s.{slug(name)}.{inflection}"


def operator_key(name: str) -> str:
    return f"o.{slug(name)}"


def reason_key(raw: str) -> str:
    return f"r.{slug(bare_reason(raw))}"


def minute_text(minute: int) -> str:
    """The words for ``m.<minute>``."""
    return "hundred hours" if minute == 0 else f"oh {spell_number(minute)}"


@functools.lru_cache(maxsize=1)
def operators() -> tuple[str, ...]:
    """The operator names to record, as the feeds spell them."""
    rows = (DATA / "operators.txt").read_text(encoding="utf-8").splitlines()
    return tuple(row.strip() for row in rows if row.strip())


@functools.lru_cache(maxsize=1)
def reasons() -> tuple[str, ...]:
    """Darwin's delay and cancellation reasons, lead-in removed."""
    rows = (DATA / "reasons.txt").read_text(encoding="utf-8").splitlines()
    return tuple(row.strip() for row in rows if row.strip())


@functools.lru_cache(maxsize=1)
def stations() -> tuple[tuple[str, str, float | None, float | None], ...]:
    """``(crs, name, lat, lon)`` from the committed station list."""
    data = json.loads(STATIONS.read_text(encoding="utf-8"))
    return tuple((row[0], row[1], row[2], row[3]) for row in data["stations"])


def core_clips() -> list[Clip]:
    """Everything but stations and reasons: no announcement works without these."""
    clips = [Clip(f"p.{name}", text, "core") for name, text in PHRASES.items()]
    clips += [Clip(f"n.{value}", f"{spell_number(value)},", "core") for value in range(60)]
    clips += [Clip(f"m.{value:02d}", f"{minute_text(value)},", "core") for value in range(10)]
    clips += [Clip(f"l.{letter.lower()}", f"{letter},", "core") for letter in "ABCDEFGHIJKLMNOP"]
    clips += [Clip(operator_key(name), f"{name},", "core") for name in operators()]
    return clips


def station_clips(names: list[str]) -> list[Clip]:
    """Both inflections of each station, one clip per distinct spoken name."""
    clips: list[Clip] = []
    seen: set[str] = set()
    for name in names:
        key = slug(name)
        if not key or key in seen:
            continue
        seen.add(key)
        spoken = spoken_station(name)
        clips.append(Clip(f"s.{key}.mid", f"{spoken},", "stations"))
        clips.append(Clip(f"s.{key}.end", f"{spoken}.", "stations"))
    return clips


def reason_clips() -> list[Clip]:
    clips: list[Clip] = []
    seen: set[str] = set()
    for reason in reasons():
        key = reason_key(reason)
        if key in seen:
            continue
        seen.add(key)
        clips.append(Clip(key, f"{reason}.", "reasons"))
    return clips


def crs_index() -> dict[str, str]:
    """CRS -> station slug, so a stop is found even where the feed's name for
    it differs from the station list's."""
    return {crs: slug(name) for crs, name, _, _ in stations()}
