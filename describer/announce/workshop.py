"""The voice workshop: a working folder in which a recorded voice is made.

The folder holds ``script/`` (what to record, in pasteable parts), ``audio/``
(what came back), ``clips/`` (one WAV per item), ``cuts.json`` (where every
clip sits in its recording, and what has been decided about it) and, once
built, a pack zip. ``scripts/voicepack.py`` drives it from a Mac and
``voices.py`` from the board's own ``/admin``.

Nothing here prints or exits: failures are a :class:`WorkshopError`, whose
text is written for the person who has to act on it. The two slow steps also
run as ``python -m describer.announce.workshop cut|build`` so the board can
give them to a low-priority subprocess.

Only the standard library and the checkout: ``decode`` reads a WAV itself and
uses ``ffmpeg`` for everything else.
"""

from __future__ import annotations

import argparse
import array
import io
import json
import logging
import math
import re
import shutil
import subprocess
import sys
import threading
import wave
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..rail.models import CallingPoint, Service, ServiceStatus
from . import catalogue, cutter
from .clips import FORMAT, MANIFEST, RATE, EveryClip, VoicePack, VoicePackError, build_plan, member
from .phrasing import AnnouncementKind

log = logging.getLogger(__name__)

AUDIO_TYPES = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aiff")
SAMPLE = "sample-service.json"
#: Where every clip was cut from, and what has been decided about it.
CUTS = "cuts.json"
#: The page that reviews cuts, served by the board and by ``voicepack.py review``.
REVIEW_PAGE = Path(__file__).resolve().parent.parent / "web" / "static" / "voice-review.html"


class WorkshopError(Exception):
    """Something the person making the voice can put right."""


class NothingToRecord(WorkshopError):
    """``--topup`` or ``--redo`` found no clip to add."""


# -- stations -----------------------------------------------------------------


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


def nearby(codes: Iterable[str], km: float) -> list[str]:
    """Station names within ``km`` of any of ``codes``, nearest first."""
    places = {crs: (lat, lon) for crs, _, lat, lon in catalogue.stations() if lat is not None}
    centres = []
    for code in codes:
        if code not in places:
            raise WorkshopError(f"{code}: not a station with a known position")
        centres.append(places[code])
    found = []
    for crs, name, _, _ in catalogue.stations():
        if crs in places:
            distance = min(_km(places[crs], centre) for centre in centres)
            if distance <= km:
                found.append((distance, name))
    return [name for _, name in sorted(found)]


def service_stations(service: Service) -> list[str]:
    names = [point.name for point in service.calling_points]
    for where in (service.destination, service.origin):
        names += re.split(r"\s+via\s+", where) if where else []
    return [name for name in names if name]


def generic_sample(names: list[str]) -> Service:
    """A plausible train for the pilot when there is no board to copy one from."""
    stops = names[1:5] or names[:1]
    return Service(
        id="sample",
        scheduled_time="14:32",
        destination=stops[-1],
        platform="2",
        status=ServiceStatus.ON_TIME,
        calling_points=[CallingPoint(name=name) for name in stops],
    )


# -- script -------------------------------------------------------------------


def _parts(clips: list[catalogue.Clip], label: str, chars: int, items: int, separator: str):
    """Split clips into pasteable parts: under ``chars`` characters and ``items`` items."""
    part: list[catalogue.Clip] = []
    size = 0
    for clip in clips:
        cost = len(clip.text) + len(separator)
        if part and (size + cost > chars or len(part) >= items):
            yield label, part
            part, size = [], 0
        part.append(clip)
        size += cost
    if part:
        yield label, part


@dataclass
class ScriptResult:
    folder: Path
    #: (file stem, items, characters) for each part written.
    rows: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(size for _, _, size in self.rows)

    def table(self) -> list[str]:
        lines, running = [], 0
        for stem, count, size in self.rows:
            running += size
            lines.append(f"| `{stem}.txt` | {count} | {size:,} | {running:,} |")
        return lines


def write_script(
    folder: Path,
    *,
    near: Iterable[str] = (),
    km: float = 20,
    seen: Iterable[tuple[str, Service]] = (),
    all_stations: bool = False,
    one_reading: bool = False,
    chars: int = 4500,
    items: int = 200,
    breaks: float = 0,
    force: bool = False,
    topup: list[dict] | None = None,
    redo: bool = False,
) -> ScriptResult:
    """Write the recording script, or add a ``topup`` / ``redo`` part to it.

    ``seen`` is what a board is showing now, as ``(mode, service)``: its
    stations are recorded and its top train becomes the pilot. ``topup`` is a
    list of ``{"key", "text"}`` a board wanted and lacked. Neither touches the
    network.
    """
    out = folder / "script"
    adding = bool(topup is not None or redo)
    if out.exists() and any(out.glob("*.json")) and not force and not adding:
        raise WorkshopError(
            f"{out} already holds a script. Audio is matched to it line by line, so it is "
            "not overwritten; use --force to replace it (and record everything again), "
            "or --topup / --redo to add to it."
        )
    separator = f' <break time="{breaks}s" />\n' if breaks else "\n\n"
    seen = list(seen)
    sample: Service | None = None
    mode = "departures"

    if adding:
        label = "topup" if topup is not None else "redo"
        if topup is not None:
            clips = [catalogue.Clip(item["key"], item["text"], label) for item in topup]
        else:
            rejected = [c for c in load_cuts(folder)["clips"].values() if c.get("rejected")]
            clips = [catalogue.Clip(c["key"], c["text"], label) for c in rejected]
        # A station is worth having both ways once it is being recorded at all,
        # unless this voice reads a name the same way wherever it falls.
        extra = []
        for clip in clips:
            if topup is not None and clip.key.startswith("s.") and not one_reading:
                stem, other = clip.key.rsplit(".", 1)[0], clip.text.rstrip(",.")
                extra += [
                    catalogue.Clip(f"{stem}.mid", f"{other},", "topup"),
                    catalogue.Clip(f"{stem}.end", f"{other}.", "topup"),
                ]
        clips = list({clip.key: clip for clip in [*clips, *extra]}.values())
        if not clips:
            raise NothingToRecord("Nothing to record: no clip is missing or rejected.")
        existing = [int(path.name[:2]) for path in out.glob("[0-9][0-9]-*.json")]
        number = max([89, *existing]) + 1
        groups = list(_parts(sorted(clips, key=lambda c: c.key), label, chars, items, separator))
    else:
        local = nearby([code.upper() for code in near], km) if near else []
        for _, service in seen:
            local = service_stations(service) + local
        everywhere = [name for _, name, _, _ in catalogue.stations()] if all_stations else []
        if not local and not everywhere:
            raise WorkshopError(
                "Name some stations: --near CRS[,CRS], --board URL or --all-stations"
            )
        local_clips = catalogue.station_clips(local)
        local_keys = {clip.key for clip in local_clips}
        rest_clips = [c for c in catalogue.station_clips(everywhere) if c.key not in local_keys]

        with_stops = [pair for pair in seen if pair[1].calling_points]
        mode, sample = with_stops[0] if with_stops else ("departures", generic_sample(local))
        known = {
            clip.key: clip
            for clip in [
                *catalogue.core_clips(),
                *catalogue.station_clips(service_stations(sample)),
            ]
        }
        plan, _ = build_plan(AnnouncementKind.ARRIVING, sample, mode, EveryClip())
        pilot = list({key: known[key] for key in plan.keys if key in known}.values())

        groups = [("pilot", pilot)]
        groups += _parts(catalogue.core_clips(), "core", chars, items, separator)
        # One reading stands in for both where the voice makes no difference
        # between them: the board plays whichever a pack holds.
        inflections = ("mid",) if one_reading else ("mid", "end")
        for inflection in inflections:
            chosen = [c for c in local_clips if c.key.endswith(inflection)]
            groups += _parts(chosen, f"local-{inflection}", chars, items, separator)
        groups += _parts(catalogue.reason_clips(), "reasons", chars, items, separator)
        for inflection in inflections:
            chosen = [c for c in rest_clips if c.key.endswith(inflection)]
            groups += _parts(chosen, f"network-{inflection}", chars, items, separator)
        number = 0
        if out.exists():
            shutil.rmtree(out)

    out.mkdir(parents=True, exist_ok=True)
    result = ScriptResult(out)
    for label, clips in groups:
        stem = f"{number:02d}-{label}"
        if breaks:
            text = "".join(clip.text + separator for clip in clips)
        else:
            text = separator.join(clip.text for clip in clips) + "\n"
        (out / f"{stem}.txt").write_text(text, encoding="utf-8")
        (out / f"{stem}.json").write_text(
            json.dumps([[clip.key, clip.text] for clip in clips], ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
        result.rows.append((stem, len(clips), len(text)))
        number += 1
    if sample is not None:
        (out / SAMPLE).write_text(
            json.dumps({"mode": mode, "service": sample.model_dump(mode="json")}), encoding="utf-8"
        )
    _write_index(out, folder)
    return result


def _write_index(out: Path, folder: Path) -> None:
    """``INDEX.md``: every part on disk, with its size."""
    lines = [
        "# Recording script",
        "",
        f"Written {date.today().isoformat()}. Paste each part into the voice, save what comes",
        "back as `audio/<number>.mp3` (part 03 is `audio/03.mp3`), then cut it.",
        "Do part 00 first and listen to it before recording the rest.",
        "",
        "| Part | Items | Characters | Running total |",
        "|---|---:|---:|---:|",
    ]
    rows = []
    for path in sorted(out.glob("[0-9][0-9]-*.json")):
        text = path.with_suffix(".txt")
        items = len(json.loads(path.read_text(encoding="utf-8")))
        rows.append(
            (path.stem, items, len(text.read_text(encoding="utf-8")) if text.is_file() else 0)
        )
    running = 0
    for stem, count, size in rows:
        running += size
        lines.append(f"| `{stem}.txt` | {count} | {size:,} | {running:,} |")
    (out / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def script_parts(folder: Path) -> list[dict]:
    """The script as parts: number, label, items and characters, in order."""
    found = []
    for path in sorted((folder / "script").glob("[0-9][0-9]-*.json")):
        try:
            items = len(json.loads(path.read_text(encoding="utf-8")))
            chars = len(path.with_suffix(".txt").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found.append(
            {"number": path.name[:2], "label": path.stem[3:], "items": items, "chars": chars}
        )
    return found


# -- audio --------------------------------------------------------------------


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _wav_header(path: Path) -> tuple[int, int] | None:
    """(channels, rate) of a plain 16-bit PCM WAV, or None for anything else."""
    try:
        with wave.open(str(path), "rb") as source:
            if source.getsampwidth() != 2:
                return None
            return source.getnchannels(), source.getframerate()
    except (wave.Error, EOFError, OSError):
        return None


def needs_ffmpeg(path: Path) -> bool:
    """True when ``decode`` cannot read this file without ``ffmpeg``."""
    return _wav_header(path) is None


def _wav_samples(path: Path) -> array.array:
    """A 16-bit PCM WAV as mono samples at the pack's rate, in pure Python."""
    with wave.open(str(path), "rb") as source:
        channels, rate = source.getnchannels(), source.getframerate()
        raw = source.readframes(source.getnframes())
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = array.array(
            "h",
            [
                sum(frame) // channels
                for frame in zip(*(samples[i::channels] for i in range(channels)), strict=False)
            ],
        )
    if rate != RATE and len(samples) > 1:
        # Straight-line interpolation: crude beside ffmpeg, adequate for speech.
        step = rate / RATE
        last = len(samples) - 1
        out = array.array("h")
        for index in range(int(len(samples) / step)):
            position = index * step
            low = int(position)
            high = min(low + 1, last)
            out.append(int(samples[low] + (samples[high] - samples[low]) * (position - low)))
        samples = out
    return samples


def decode(path: Path) -> array.array:
    """Any audio file as 16-bit mono samples at the pack's rate.

    A WAV already at that rate is read directly; ``ffmpeg`` does everything else
    (a WAV in another rate falls back to a plain conversion when it is absent).
    """
    header = _wav_header(path)
    if header == (1, RATE):
        return _wav_samples(path)
    if not has_ffmpeg():
        if header is not None:
            return _wav_samples(path)
        raise WorkshopError(
            "This recording needs ffmpeg to be read and it is not installed "
            "(Pi: sudo apt install ffmpeg; Mac: brew install ffmpeg). A WAV file works without it."
        )
    command = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE)]
    result = subprocess.run([*command, "-f", "s16le", "-"], capture_output=True, check=False)
    if result.returncode:
        raise cutter.CutError(result.stderr.decode(errors="replace").strip()[:200])
    samples = array.array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def write_wav(path: Path, samples: array.array) -> None:
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(RATE)
        sink.writeframes(samples.tobytes())


def audio_for(audio_dir: Path, number: str) -> Path | None:
    """The recording of part ``number``: ``03.mp3``, ``03 take 2.mp3``, ``3.wav``."""
    if not audio_dir.is_dir():
        return None
    pattern = re.compile(rf"^0*{int(number)}(?!\d)")
    found = sorted(
        path
        for path in audio_dir.iterdir()
        if path.suffix.lower() in AUDIO_TYPES and pattern.match(path.stem)
    )
    return found[-1] if found else None


# -- cuts ---------------------------------------------------------------------


def load_cuts(folder: Path) -> dict:
    """``cuts.json``: each part's recording, and each clip's place in it."""
    try:
        cuts = json.loads((folder / CUTS).read_text(encoding="utf-8"))
        cuts.setdefault("parts", {})
        cuts.setdefault("clips", {})
        return cuts
    except (OSError, ValueError, AttributeError):
        return {"parts": {}, "clips": {}}


def save_cuts(folder: Path, cuts: dict) -> None:
    partial = folder / (CUTS + ".partial")
    partial.write_text(json.dumps(cuts, ensure_ascii=False, indent=1), encoding="utf-8")
    partial.replace(folder / CUTS)


@dataclass
class PartResult:
    number: str
    stem: str
    source: str = ""
    items: int = 0
    ok: bool = False
    error: str = ""
    usual_pause_ms: int = 0
    shortest_pause_ms: int = 0
    doubtful: int = 0
    kept: int = 0


@dataclass
class CutSummary:
    parts: list[PartResult] = field(default_factory=list)
    waiting: int = 0
    clips_dir: Path = Path()

    @property
    def cut(self) -> int:
        return sum(1 for part in self.parts if part.ok)

    @property
    def failed(self) -> int:
        return sum(1 for part in self.parts if not part.ok)

    @property
    def doubts(self) -> int:
        return sum(part.doubtful for part in self.parts)


def cut_folder(
    folder: Path,
    only: Iterable[str] = (),
    on_part: Callable[[PartResult], None] | None = None,
) -> CutSummary:
    """Cut every part that has a recording (or just ``only``) into clips."""
    script, audio, clips_dir = folder / "script", folder / "audio", folder / "clips"
    parts = sorted(script.glob("[0-9][0-9]-*.json"))
    if not parts:
        raise WorkshopError(f"No script in {script}. Write the script first.")
    audio.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(only)
    summary = CutSummary(clips_dir=clips_dir)
    for part in parts:
        number = part.name[:2]
        if wanted and number not in wanted:
            continue
        items = json.loads(part.read_text(encoding="utf-8"))
        source = audio_for(audio, number)
        if source is None:
            summary.waiting += 1
            continue
        result = PartResult(number, part.stem, source=source.name, items=len(items))
        summary.parts.append(result)
        stamp = source.stat().st_mtime
        # Read the cuts afresh for each part: a review in another thread may have
        # saved a decision since the last one.
        cuts = load_cuts(folder)
        same_take = (cuts["parts"].get(number) or {}).get("mtime") == stamp
        try:
            samples = decode(source)
            cut = cutter.find_items(samples, RATE, [text for _, text in items])
        except cutter.CutError as exc:
            result.error = str(exc)
            if on_part:
                on_part(result)
            continue
        gain = cutter.gain_for(samples, RATE)
        cuts = load_cuts(folder)
        cuts["parts"][number] = {
            "name": part.stem,
            "source": source.name,
            "mtime": stamp,
            "gain": gain,
            "seconds": len(samples) / RATE,
            "usual_pause_ms": cut.usual_pause_ms,
            "shortest_pause_ms": cut.shortest_pause_ms,
        }
        for index, ((key, text), segment) in enumerate(zip(items, cut.segments, strict=True)):
            old = cuts["clips"].get(key) or {}
            # A cut moved by hand is kept for as long as the recording is the same one.
            by_hand = same_take and old.get("part") == number and old.get("manual")
            if by_hand:
                segment = (old["start"], old["end"])
                result.kept += 1
            cuts["clips"][key] = {
                "key": key,
                "text": text,
                "part": number,
                "index": index,
                "start": segment[0],
                "end": segment[1],
                "doubtful": index in cut.doubtful and not by_hand,
                "manual": bool(by_hand),
                "rejected": bool(same_take and old.get("part") == number and old.get("rejected")),
            }
            write_wav(clips_dir / f"{key}.wav", cutter.extract(samples, segment, RATE, gain))
        save_cuts(folder, cuts)
        result.ok = True
        result.usual_pause_ms = cut.usual_pause_ms
        result.shortest_pause_ms = cut.shortest_pause_ms
        result.doubtful = len(cut.doubtful)
        if on_part:
            on_part(result)
    return summary


def forget_part(folder: Path, number: str) -> None:
    """Drop a part's recording, its clips and its cut entries."""
    audio = folder / "audio"
    while (source := audio_for(audio, number)) is not None:
        source.unlink(missing_ok=True)
    cuts = load_cuts(folder)
    cuts["parts"].pop(number, None)
    for key in [k for k, clip in cuts["clips"].items() if clip.get("part") == number]:
        del cuts["clips"][key]
        (folder / "clips" / f"{key}.wav").unlink(missing_ok=True)
    save_cuts(folder, cuts)


# -- review -------------------------------------------------------------------


class DecodedCache:
    """One decoded recording, kept while it is being reviewed.

    Shared by every voice on the board so that memory holds one at most.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: tuple[Path, float, array.array] | None = None

    def get(self, source: Path) -> array.array:
        stamp = source.stat().st_mtime
        with self._lock:
            held = self._held
            if held is None or held[0] != source or held[1] != stamp:
                held = (source, stamp, decode(source))
                self._held = held
            return held[2]

    def clear(self) -> None:
        with self._lock:
            self._held = None


class Review:
    """The working folder as the review page sees it."""

    def __init__(self, folder: Path, cache: DecodedCache | None = None) -> None:
        self.folder = folder
        self._cache = cache or DecodedCache()

    def cuts(self) -> dict:
        return {**load_cuts(self.folder), "rate": RATE}

    def samples(self, part: str) -> array.array:
        """A part's recording, decoded once and again only if the file changes."""
        info = load_cuts(self.folder)["parts"][part]
        source = self.folder / "audio" / info["source"]
        try:
            return self._cache.get(source)
        except OSError as exc:
            raise WorkshopError("The recording for this part has been removed.") from exc

    def clip(self, key: str) -> bytes:
        """A clip's WAV. Only keys ``cuts.json`` knows are ever looked up."""
        if key not in load_cuts(self.folder)["clips"]:
            raise KeyError(key)
        return (self.folder / "clips" / f"{key}.wav").read_bytes()

    def window(self, part: str, start: int, end: int) -> bytes:
        """Part of a recording as a WAV, for the page to draw and play."""
        samples = self.samples(part)
        start, end = max(0, start), min(len(samples), end)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(RATE)
            sink.writeframes(samples[start:end].tobytes())
        return buffer.getvalue()

    def update(self, key: str, change: dict) -> dict:
        """Reject or restore a clip, or move its cut and write the clip again."""
        cuts = load_cuts(self.folder)
        clip = cuts["clips"][key]
        if "rejected" in change:
            clip["rejected"] = bool(change["rejected"])
        if change.get("start") is not None and change.get("end") is not None:
            samples = self.samples(clip["part"])
            start = max(0, min(int(change["start"]), len(samples) - 1))
            end = max(start + 1, min(int(change["end"]), len(samples)))
            clip.update(start=start, end=end, manual=True, doubtful=False, rejected=False)
            gain = cuts["parts"][clip["part"]]["gain"]
            write_wav(
                self.folder / "clips" / f"{key}.wav",
                cutter.extract(samples, (start, end), RATE, gain),
            )
        if change.get("checked"):
            clip["doubtful"] = False
        save_cuts(self.folder, cuts)
        return clip


# -- build --------------------------------------------------------------------


@dataclass
class BuildResult:
    target: Path
    clips: int
    size: int
    stations: int
    reasons: int
    core_missing: list[str]
    rejected: int
    unchecked: int


def build_pack(folder: Path, name: str, out: Path) -> BuildResult:
    """Zip the clips into a voice pack at ``out``, replacing it only once it reads back."""
    clips_dir = folder / "clips"
    known = load_cuts(folder)["clips"]
    rejected = {key for key, clip in known.items() if clip.get("rejected")}
    unchecked = sum(1 for clip in known.values() if clip.get("doubtful"))
    files = sorted(path for path in clips_dir.glob("*.wav") if path.stem not in rejected)
    if not files:
        raise WorkshopError(f"No clips in {clips_dir}. Cut the recordings first.")
    have = {path.stem for path in files}
    core_missing = [clip.key for clip in catalogue.core_clips() if clip.key not in have]
    manifest = {
        "format": FORMAT,
        "name": name,
        "rate": RATE,
        "created": date.today().isoformat(),
        "crs": {
            crs: slug
            for crs, slug in catalogue.crs_index().items()
            if f"s.{slug}.mid" in have or f"s.{slug}.end" in have
        },
    }
    partial = out.with_name(out.name + ".partial")
    # Stored, not deflated: speech barely compresses, and the Pi reads clips in place.
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False))
            for path in files:
                archive.write(path, member(path.stem))
        try:
            pack = VoicePack(partial)
        except VoicePackError as exc:
            raise WorkshopError(str(exc)) from exc
        partial.replace(out)
    finally:
        partial.unlink(missing_ok=True)
    return BuildResult(
        target=out,
        clips=len(pack),
        size=out.stat().st_size,
        stations=len({key.rsplit(".", 1)[0] for key in have if key.startswith("s.")}),
        reasons=sum(1 for key in have if key.startswith("r.")),
        core_missing=core_missing,
        rejected=len(rejected),
        unchecked=unchecked,
    )


# -- subprocess entry points --------------------------------------------------


def run(argv: list[str]) -> int:
    """``cut`` and ``build`` as the board's job runner calls them.

    Errors go to stderr as plain text; the exit code says whether it worked.
    """
    parser = argparse.ArgumentParser(prog="python -m describer.announce.workshop")
    commands = parser.add_subparsers(dest="command", required=True)
    cut = commands.add_parser("cut")
    cut.add_argument("--dir", type=Path, required=True)
    cut.add_argument("--only", nargs="*", default=[])
    build = commands.add_parser("build")
    build.add_argument("--dir", type=Path, required=True)
    build.add_argument("--name", required=True)
    build.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "cut":
            summary = cut_folder(args.dir, args.only)
            failures = [part for part in summary.parts if not part.ok]
            if failures:
                print("; ".join(f"part {p.number}: {p.error}" for p in failures), file=sys.stderr)
                return 1
        else:
            build_pack(args.dir, args.name, args.out or args.dir / "pack.zip")
    except (WorkshopError, cutter.CutError, VoicePackError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def iter_files(folder: Path) -> Iterator[Path]:
    """Every file under ``folder`` (for sizes)."""
    if folder.is_dir():
        yield from (path for path in folder.rglob("*") if path.is_file())


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
