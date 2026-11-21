"""Announcements in a recorded voice: a voice pack, and the plan that plays it.

A voice pack is one zip of short WAV clips (``catalogue.py`` lists them) and a
``manifest.json``. An announcement becomes a *plan*: clip keys and pauses, in
order. The plan is built from the same :class:`Service` the Piper wording is
built from, and says the same words.

Nothing here is required. With no pack, a pack switched off, or a plan that
needs a clip the pack does not hold, the announcement is spoken by Piper as it
always was. A voice that changes half way through a sentence is worse than
either voice, so the fallback is the whole announcement, never one word of it.
What was missing is remembered so a top-up script can be recorded.
"""

from __future__ import annotations

import array
import json
import logging
import re
import wave
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..config import AnnouncementsConfig
from ..rail.models import Service
from . import catalogue
from .phrasing import AnnouncementKind, calling_points

log = logging.getLogger(__name__)

#: Every clip in a pack is 16-bit mono at this rate, so joining them is
#: joining bytes. It is Piper's rate and the chime's.
RATE = 22050
MANIFEST = "manifest.json"
FORMAT = 1
#: Refuse an upload beyond this; the whole network at two clips a station is
#: about a quarter of it.
MAX_PACK_BYTES = 1024 * 1024 * 1024

#: Silences between clips, in milliseconds. The clips carry a little air of
#: their own; these are what a breath adds on top.
GAP = 30
LIST_GAP = 140
SENTENCE_GAP = 450

#: Remember at most this many missing clips; a board with no stations recorded
#: at all should not grow the list without end.
MAX_MISSING = 300

_PLATFORM = re.compile(r"^(\d{1,2})?([A-Za-z])?$")
_VIA = re.compile(r"\s+via\s+", re.IGNORECASE)
_AND = re.compile(r"\s+(?:&|and)\s+")

Part = str | int


class VoicePackError(RuntimeError):
    """The pack is not one this board can play."""


class Clips(Protocol):
    """What a plan needs to know about the clips on hand."""

    crs: dict[str, str]

    def has(self, key: str) -> bool: ...


class EveryClip:
    """Pretends every clip exists: for the recording script and the tests."""

    crs: dict[str, str] = {}

    def has(self, key: str) -> bool:
        return True


@dataclass
class Plan:
    """An announcement as clips and pauses. Unplayable while ``missing`` is not empty."""

    parts: list[Part] = field(default_factory=list)
    #: Clip key -> the words to record for it.
    missing: dict[str, str] = field(default_factory=dict)

    @property
    def playable(self) -> bool:
        return bool(self.parts) and not self.missing

    @property
    def keys(self) -> list[str]:
        return [part for part in self.parts if isinstance(part, str)]


class _Builder:
    def __init__(self, clips: Clips) -> None:
        self.clips = clips
        self.plan = Plan()
        #: Clips left out without spoiling the sentence: key -> words.
        self.optional: dict[str, str] = {}

    def say(self, *keys: str) -> None:
        for key in keys:
            if self.plan.parts and not isinstance(self.plan.parts[-1], int):
                self.plan.parts.append(GAP)
            self.plan.parts.append(key)

    def pause(self, milliseconds: int) -> None:
        if self.plan.parts and isinstance(self.plan.parts[-1], int):
            self.plan.parts[-1] = max(self.plan.parts[-1], milliseconds)
        elif self.plan.parts:
            self.plan.parts.append(milliseconds)

    # -- the pieces of a sentence ------------------------------------------

    def _station_key(self, name: str, crs: str | None, inflection: str) -> str | None:
        """The clip for a station, in this inflection if the pack has it."""
        slugs = [catalogue.slug(name)]
        if crs and self.clips.crs.get(crs.upper()):
            slugs.append(self.clips.crs[crs.upper()])
        other = "end" if inflection == "mid" else "mid"
        for wanted in (inflection, other):
            for slug in slugs:
                if slug and self.clips.has(f"s.{slug}.{wanted}"):
                    return f"s.{slug}.{wanted}"
        return None

    def station(self, name: str, crs: str | None, inflection: str) -> None:
        key = self._station_key(name, crs, inflection)
        if key is None:
            key = catalogue.station_key(name, inflection)
            mark = "." if inflection == "end" else ","
            self.plan.missing[key] = catalogue.spoken_station(name) + mark
        self.say(key)

    def place(self, text: str, crs: str | None, inflection: str) -> None:
        """A destination or origin: one station, or "A and B" for a train that divides."""
        if self._station_key(text, crs, inflection) is not None:
            self.station(text, crs, inflection)
            return
        names = [name for name in _AND.split(text) if name.strip()]
        if len(names) < 2 or any(self._station_key(n, None, "mid") is None for n in names):
            self.station(text, crs, inflection)
            return
        for name in names[:-1]:
            self.station(name, None, "mid")
        self.say("p.and")
        self.station(names[-1], None, inflection)

    def time(self, hhmm: str | None) -> None:
        try:
            hours, minutes = (int(piece) for piece in (hhmm or "").split(":", 1))
        except ValueError:
            return
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            return
        self.say(f"n.{hours}", f"m.{minutes:02d}" if minutes < 10 else f"n.{minutes}")

    def service(self, service: Service, mode: str, inflection: str) -> None:
        """ "fourteen thirty two / Great Western Railway / service to / London Paddington"."""
        self.time(service.scheduled_time)
        if service.operator:
            key = catalogue.operator_key(service.operator)
            if self.clips.has(key):
                self.say(key)
            else:
                # Left out rather than handed to Piper: the sentence stands without it.
                self._note(key, f"{service.operator},")
        arrivals = mode == "arrivals"
        where = service.origin if arrivals else service.destination
        if not where:
            # "the 14:32 service." needs a clip nobody recorded.
            self.plan.missing["s..end"] = ""
            return
        self.say("p.service_from" if arrivals else "p.service_to")
        head, *via = _VIA.split(where, maxsplit=1)
        crs = None
        if not arrivals and service.calling_points and service.calling_points[-1].name == head:
            crs = service.calling_points[-1].crs
        via_text = via[0] if via else ""
        via_ok = bool(via_text) and self._station_key(via_text, None, inflection) is not None
        if via_text and not via_ok:
            self._note(
                catalogue.station_key(via_text, "mid"), catalogue.spoken_station(via_text) + ","
            )
        self.place(head, crs, "mid" if via_ok else inflection)
        if via_ok:
            self.say("p.via")
            self.station(via_text, None, inflection)

    def reason(self, raw: str | None) -> None:
        if not raw:
            return
        key = catalogue.reason_key(raw)
        if not self.clips.has(key):
            self._note(key, catalogue.bare_reason(raw) + ".")
            return
        self.pause(SENTENCE_GAP)
        self.say("p.due_to", key)

    def _note(self, key: str, text: str) -> None:
        """Something optional was left out. Worth recording, not worth a fallback."""
        self.optional[key] = text


def build_plan(
    kind: AnnouncementKind,
    service: Service,
    mode: str,
    clips: Clips,
    *,
    max_calling_points: int = 8,
) -> tuple[Plan, dict[str, str]]:
    """The plan for one announcement, and the optional clips it did without.

    The wording follows ``phrasing.build_text`` clause for clause. Two things
    differ on purpose: an operator or a reason with no clip is left out, and a
    delay of an hour or more is announced without its length.
    """
    builder = _Builder(clips)
    say, pause = builder.say, builder.pause

    if kind is AnnouncementKind.ARRIVING:
        stops = calling_points(service, max_calling_points) if mode == "departures" else []
        match = _PLATFORM.fullmatch((service.platform or "").strip())
        number, letter = match.groups() if match else (None, None)
        if number is not None and int(number) > 59:
            number = letter = None
        if number or letter:
            say("p.next_platform")
            if number:
                say(f"n.{int(number)}")
            if letter:
                say(f"l.{letter.lower()}")
        else:
            say("p.next_station")
        say("p.will_be_the")
        builder.service(service, mode, "mid" if stops else "end")
        if stops:
            pause(LIST_GAP)
            say("p.calling_at")
            for point in stops[:-1]:
                builder.station(point.name, point.crs, "mid")
                pause(LIST_GAP)
            if len(stops) > 1:
                say("p.and")
            builder.station(stops[-1].name, stops[-1].crs, "end")
        if service.status.value == "expected" and 1 <= service.delay_minutes <= 59:
            pause(SENTENCE_GAP)
            say("p.running_late", f"n.{service.delay_minutes}")
            say("p.minute_late" if service.delay_minutes == 1 else "p.minutes_late")

    elif kind is AnnouncementKind.DELAYED:
        say("p.sorry_the")
        builder.service(service, mode, "mid")
        if 1 <= service.delay_minutes <= 59:
            say("p.is_delayed_by", f"n.{service.delay_minutes}")
            say("p.minute" if service.delay_minutes == 1 else "p.minutes")
        else:
            say("p.is_delayed")
        builder.reason(service.delay_reason)
        pause(SENTENCE_GAP)
        say("p.apologise_delay")

    else:
        say("p.sorry_the")
        builder.service(service, mode, "mid")
        say("p.has_been_cancelled")
        builder.reason(service.cancel_reason)
        pause(SENTENCE_GAP)
        say("p.apologise_inconvenience")

    plan = builder.plan
    for key in plan.keys:
        if key not in plan.missing and not clips.has(key):
            plan.missing[key] = ""
    return plan, builder.optional


# -- the pack ----------------------------------------------------------------


def member(key: str) -> str:
    """Where a clip lives inside the zip."""
    return f"clips/{key}.wav"


class VoicePack:
    """One zip of clips, read in place: nothing is unpacked on the Pi."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read(MANIFEST))
                names = set(archive.namelist())
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise VoicePackError(f"Not a voice pack: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise VoicePackError("Voice pack is in a format this version does not read")
        if manifest.get("rate") != RATE:
            raise VoicePackError(f"Voice pack clips must be {RATE} Hz")
        self.name = str(manifest.get("name") or path.stem)
        self.created = str(manifest.get("created") or "")
        self.crs: dict[str, str] = dict(manifest.get("crs") or {})
        prefix, suffix = "clips/", ".wav"
        self._keys = {
            name[len(prefix) : -len(suffix)]
            for name in names
            if name.startswith(prefix) and name.endswith(suffix)
        }
        if not self._keys:
            raise VoicePackError("Voice pack holds no clips")
        self.mtime = path.stat().st_mtime

    def has(self, key: str) -> bool:
        return key in self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def render(self, parts: list[Part], target: Path, volume: float = 1.0) -> Path:
        """Join a plan's clips and pauses into one WAV at ``target``."""
        pcm = bytearray()
        with zipfile.ZipFile(self.path) as archive:
            for part in parts:
                if isinstance(part, int):
                    pcm += bytes(2 * (RATE * part // 1000))
                    continue
                with archive.open(member(part)) as handle, wave.open(handle, "rb") as clip:
                    if (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) != (
                        RATE,
                        1,
                        2,
                    ):
                        raise VoicePackError(f"Clip {part} is not {RATE} Hz 16-bit mono")
                    pcm += clip.readframes(clip.getnframes())
        if volume < 0.999:
            samples = array.array("h")
            samples.frombytes(bytes(pcm))
            pcm = bytearray(array.array("h", [int(s * volume) for s in samples]).tobytes())
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial.wav")
        with wave.open(str(partial), "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(RATE)
            sink.writeframes(bytes(pcm))
        partial.replace(target)
        return target


class RecordedVoice:
    """The pack named in the config, reloaded when the file changes."""

    def __init__(self, config: AnnouncementsConfig) -> None:
        self._config = config
        self._pack: VoicePack | None = None
        self._error: str | None = None
        self._seen: tuple[str, float] | None = None
        #: Clips an announcement wanted and the pack did not have: key -> words.
        self.missing: dict[str, str] = {}

    def update_config(self, config: AnnouncementsConfig) -> None:
        self._config = config

    @property
    def path(self) -> Path:
        return Path(self._config.voice_pack)

    def pack(self) -> VoicePack | None:
        """The pack to use now, or None: switched off, absent or unreadable."""
        if not self._config.use_voice_pack or not self._config.voice_pack:
            return None
        path = self.path
        try:
            stamp = (str(path), path.stat().st_mtime)
        except OSError:
            self._pack, self._seen, self._error = None, None, None
            return None
        if stamp != self._seen:
            self._seen = stamp
            try:
                self._pack, self._error = VoicePack(path), None
                self.missing.clear()
                log.info("Voice pack %s: %d clips", self._pack.name, len(self._pack))
            except VoicePackError as exc:
                self._pack, self._error = None, str(exc)
                log.warning("%s", exc)
        return self._pack

    def plan(
        self, kind: AnnouncementKind, service: Service, mode: str, max_calling_points: int
    ) -> Plan | None:
        """A playable plan, or None when Piper should speak this one."""
        pack = self.pack()
        if pack is None:
            return None
        plan, optional = build_plan(
            kind, service, mode, pack, max_calling_points=max_calling_points
        )
        for key, text in {**optional, **plan.missing}.items():
            if text and len(self.missing) < MAX_MISSING:
                self.missing.setdefault(key, text)
        if not plan.playable:
            log.info("No clip for %s; Piper speaks this one", ", ".join(plan.missing))
            return None
        return plan

    def status(self) -> dict[str, object]:
        """For /admin: is there a pack, and what has it been asked for and lacked."""
        pack = self.pack()
        return {
            "enabled": self._config.use_voice_pack,
            "installed": pack is not None,
            "name": pack.name if pack else None,
            "created": pack.created if pack else None,
            "clips": len(pack) if pack else 0,
            "error": self._error,
            "missing": [{"key": key, "text": text} for key, text in self.missing.items()],
        }
