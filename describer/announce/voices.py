"""Voices made on the board: a folder each, and the jobs that cut and build them.

``root/<slug>/`` holds ``voice.json`` and the working folder ``workshop.py``
describes, plus the built pack as ``pack.zip``. The slow steps (cutting a
recording, zipping a pack) run one at a time as low-priority subprocesses, so
the board keeps announcing while a voice is being made.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import re
import shutil
import sys
import zipfile
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from ..rail.models import Service
from . import catalogue, workshop
from .clips import VoicePack, VoicePackError

log = logging.getLogger(__name__)

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
PART = re.compile(r"^[0-9]{2}$")
#: Audio the board will take for a part.
UPLOAD_TYPES = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
MAX_AUDIO_BYTES = 300 * 1024 * 1024
#: Never let an upload leave less than this free.
MIN_FREE_BYTES = 500 * 1024 * 1024
PACK = "pack.zip"
META = "voice.json"
#: Pasting limits the voice makers offer: (characters, items) in one part.
PASTE_LIMITS = {5000: (4500, 200), 100000: (100000, 600)}


class VoiceError(Exception):
    """A request the board refuses, with the HTTP status and words for the user."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# -- jobs ---------------------------------------------------------------------

#: An executor takes the workshop's arguments and returns (exit code, error text).
Executor = Callable[[list[str]], tuple[int, str]]


@dataclass
class Job:
    slug: str
    #: "cut" or "build"
    kind: str
    part: str
    args: list[str]


class JobRunner:
    """One job at a time, in the order asked. State lives in memory only.

    Normally each job is a subprocess of ``python -m describer.announce.workshop``
    at low priority. Given ``inline``, a plain function, each job instead runs
    to completion inside ``submit`` and no event loop is needed: for tests.
    """

    def __init__(self, inline: Executor | None = None) -> None:
        self.inline = inline
        self._queue: deque[Job] = deque()
        self._running: Job | None = None
        self._worker: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        #: (slug, part) or (slug, "build") -> message
        self.failed: dict[tuple[str, str], str] = {}

    # -- asking ----------------------------------------------------------

    def submit(self, job: Job) -> None:
        key = (job.slug, job.part)
        self.failed.pop(key, None)
        if any(queued.slug == job.slug and queued.part == job.part for queued in self._queue):
            return
        self._queue.append(job)
        if self.inline is not None:
            self._draininline()
        elif self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._work())

    def _draininline(self) -> None:
        assert self.inline is not None
        while self._queue:
            job = self._running = self._queue.popleft()
            try:
                code, message = self.inline(job.args)
            except Exception as exc:  # a test double failing
                code, message = 1, str(exc)
            self._finish(job, code, message)
        self._running = None

    async def _work(self) -> None:
        try:
            while self._queue:
                job = self._running = self._queue.popleft()
                code, message = await self._spawn(job.args)
                self._finish(job, code, message)
        finally:
            self._running = None

    async def _spawn(self, args: list[str]) -> tuple[int, str]:
        command = [sys.executable, "-m", "describer.announce.workshop", *args]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=_low_priority if os.name == "posix" else None,  # noqa: PLW1509
            )
            _, stderr = await self._process.communicate()
            code = self._process.returncode or 0
        except OSError as exc:
            return 1, f"Could not start the job: {exc}"
        finally:
            self._process = None
        text = stderr.decode(errors="replace").strip()[:500]
        return code, text or ("The job stopped unexpectedly." if code else "")

    def _finish(self, job: Job, code: int, message: str) -> None:
        if code:
            self.failed[(job.slug, job.part)] = message or "The job failed."
            log.warning("Voice %s: %s %s failed", job.slug, job.kind, job.part)
        else:
            log.info("Voice %s: %s %s done", job.slug, job.kind, job.part)

    # -- asking about it -------------------------------------------------

    def part_state(self, slug: str, part: str) -> tuple[str, str | None] | None:
        """("cutting" | "queued" | "failed", message) or None when nothing is happening."""
        if self._running and (self._running.slug, self._running.part) == (slug, part):
            return "cutting", None
        if any(job.slug == slug and job.part == part for job in self._queue):
            return "queued", None
        if (slug, part) in self.failed:
            return "failed", self.failed[(slug, part)]
        return None

    def building(self, slug: str) -> bool:
        return self.is_busy(slug, kind="build")

    def build_error(self, slug: str) -> str | None:
        return self.failed.get((slug, "build"))

    def is_busy(self, slug: str, kind: str | None = None) -> bool:
        jobs = [*self._queue, *([self._running] if self._running else [])]
        return any(job.slug == slug and (kind is None or job.kind == kind) for job in jobs)

    # -- stopping --------------------------------------------------------

    async def close(self) -> None:
        self._queue.clear()
        if self._process is not None:
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._worker
            self._worker = None


def _low_priority() -> None:
    with contextlib.suppress(OSError):
        os.nice(10)


# -- the store ----------------------------------------------------------------


class VoiceStore:
    """Every voice made on this board."""

    def __init__(self, root: Path, runner: JobRunner | None = None) -> None:
        self.root = root
        self.jobs = runner or JobRunner()
        self.cache = workshop.DecodedCache()

    # -- paths and checks ------------------------------------------------

    def folder(self, slug: str) -> Path:
        """A voice's folder, after the slug has been checked against the pattern."""
        if not SLUG.fullmatch(slug or ""):
            raise VoiceError("There is no voice with that name.", 404)
        folder = self.root / slug
        if not (folder / META).is_file():
            raise VoiceError("There is no voice with that name.", 404)
        return folder

    @staticmethod
    def check_part(number: str) -> str:
        if not PART.fullmatch(number or ""):
            raise VoiceError("That is not a part number.", 404)
        return number

    def _part(self, slug: str, number: str) -> Path:
        folder = self.folder(slug)
        self.check_part(number)
        if not any((folder / "script").glob(f"{number}-*.json")):
            raise VoiceError("This voice has no such part in its script.", 404)
        return folder

    def free_bytes(self) -> int:
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return shutil.disk_usage(probe).free

    def _meta(self, folder: Path) -> dict:
        try:
            return json.loads((folder / META).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_meta(self, folder: Path, meta: dict) -> None:
        partial = folder / (META + ".partial")
        partial.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(folder / META)

    # -- reading ---------------------------------------------------------

    def slugs(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            path.name
            for path in self.root.iterdir()
            if SLUG.fullmatch(path.name) and (path / META).is_file()
        )

    def list(self, in_use: str = "") -> list[dict]:
        return [self.summary(slug, in_use) for slug in self.slugs()]

    def summary(self, slug: str, in_use: str = "") -> dict:
        folder = self.folder(slug)
        meta = self._meta(folder)
        cuts = workshop.load_cuts(folder)
        pack = folder / PACK
        script = workshop.script_parts(folder)
        recorded = sum(1 for part in script if workshop.audio_for(folder / "audio", part["number"]))
        clips = [c for c in cuts["clips"].values() if not c.get("rejected")]
        return {
            "slug": slug,
            "name": meta.get("name", slug),
            "created": meta.get("created", ""),
            "imported": bool(meta.get("imported")),
            "in_use": slug == in_use,
            "built": pack.is_file(),
            "stale": self._stale(folder),
            "pack_bytes": _size(pack),
            "working_bytes": sum(
                _size(path)
                for sub in ("audio", "clips")
                for path in workshop.iter_files(folder / sub)
            ),
            "clips": len(clips),
            "to_check": sum(1 for c in clips if c.get("doubtful")),
            "rejected": sum(1 for c in cuts["clips"].values() if c.get("rejected")),
            "parts_total": len(script),
            "parts_recorded": recorded,
            "busy": self.jobs.is_busy(slug),
        }

    @staticmethod
    def _stale(folder: Path) -> bool:
        pack = folder / PACK
        try:
            built = pack.stat().st_mtime
        except OSError:
            return False
        newest = [(folder / workshop.CUTS)]
        newest += list((folder / "clips").glob("*.wav")) if (folder / "clips").is_dir() else []
        return any(_mtime(path) > built for path in newest)

    def detail(self, slug: str, in_use: str = "") -> dict:
        folder = self.folder(slug)
        meta = self._meta(folder)
        cuts = workshop.load_cuts(folder)
        pack = folder / PACK
        parts = []
        for part in workshop.script_parts(folder):
            number = part["number"]
            source = workshop.audio_for(folder / "audio", number)
            info = cuts["parts"].get(number) or {}
            mine = [c for c in cuts["clips"].values() if c.get("part") == number]
            audio = None
            if source is not None:
                cut_this = info.get("source") == source.name and info.get("mtime") == _mtime(source)
                audio = {
                    "name": (meta.get("uploads") or {}).get(number) or source.name,
                    "bytes": _size(source),
                    "seconds": info.get("seconds") if cut_this else None,
                }
            job = self.jobs.part_state(slug, number)
            error = None
            if job:
                state, error = job
            elif source is None:
                state = "cut" if info else "waiting"
            elif cut_this:
                state = "cut"
            else:
                state = "failed"
                error = "The cut did not finish. Press Cut again."
            parts.append(
                {
                    **part,
                    "audio": audio,
                    "state": state,
                    "clips": len([c for c in mine if not c.get("rejected")]),
                    "to_check": sum(1 for c in mine if c.get("doubtful") and not c.get("rejected")),
                    "error": error,
                    "usual_pause_ms": info.get("usual_pause_ms"),
                    "shortest_pause_ms": info.get("shortest_pause_ms"),
                }
            )
        built_at = None
        if pack.is_file():
            built_at = datetime.fromtimestamp(pack.stat().st_mtime, UTC).isoformat(
                timespec="seconds"
            )
        return {
            **self.summary(slug, in_use),
            "built_at": built_at,
            "building": self.jobs.building(slug),
            "build_error": self.jobs.build_error(slug),
            "parts": parts,
        }

    def pack_path(self, slug: str) -> Path:
        return self.folder(slug) / PACK

    # -- making ----------------------------------------------------------

    def create(
        self,
        name: str,
        *,
        near: Iterable[str] = (),
        km: float = 20,
        seen: Iterable[tuple[str, Service]] = (),
        all_stations: bool = False,
        one_reading: bool = False,
        paste_limit: int = 5000,
        options: dict | None = None,
    ) -> str:
        name = " ".join(name.split())
        slug = catalogue.slug(name)[:40].strip("-")
        if not name or not SLUG.fullmatch(slug):
            raise VoiceError("Give the voice a name made of letters or numbers.", 422)
        if paste_limit not in PASTE_LIMITS:
            raise VoiceError("Choose how much your voice maker takes in one go.", 422)
        folder = self.root / slug
        if folder.exists():
            raise VoiceError(f"There is already a voice called {name}. Choose another name.", 409)
        chars, items = PASTE_LIMITS[paste_limit]
        folder.mkdir(parents=True)
        try:
            workshop.write_script(
                folder,
                near=[code.upper() for code in near],
                km=km,
                seen=list(seen),
                all_stations=all_stations,
                one_reading=one_reading,
                chars=chars,
                items=items,
            )
            self._save_meta(
                folder,
                {
                    "name": name,
                    "slug": slug,
                    "created": date.today().isoformat(),
                    "imported": False,
                    "options": {
                        **(options or {}),
                        "near": [c.upper() for c in near],
                        "km": km,
                        "all_stations": all_stations,
                        "one_reading": one_reading,
                        "paste_limit": paste_limit,
                    },
                },
            )
        except workshop.WorkshopError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise VoiceError(_station_words(str(exc)), 422) from exc
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        log.info("Voice %s created", slug)
        return slug

    def import_pack(self, partial: Path) -> str:
        """Keep a ready-made zip as a voice, if it reads as a voice pack."""
        try:
            pack = VoicePack(partial)
        except VoicePackError as exc:
            raise VoiceError(
                f"That file is not a voice pack this board can use. {exc}", 422
            ) from exc
        name = " ".join(pack.name.split())[:60] or "Imported voice"
        base = catalogue.slug(name)[:34].strip("-") or "imported"
        slug, number = base, 1
        while (self.root / slug).exists():
            number += 1
            slug = f"{base}-{number}"
        folder = self.root / slug
        folder.mkdir(parents=True)
        shutil.move(str(partial), folder / PACK)
        self._save_meta(
            folder,
            {
                "name": name if number == 1 else f"{name} ({number})",
                "slug": slug,
                "created": date.today().isoformat(),
                "imported": True,
                "options": {},
            },
        )
        log.info("Voice %s imported (%d clips)", slug, len(pack))
        return slug

    def delete(self, slug: str) -> None:
        folder = self.folder(slug)
        self._not_busy(slug)
        shutil.rmtree(folder)
        log.info("Voice %s deleted", slug)

    def free_working(self, slug: str) -> None:
        folder = self.folder(slug)
        self._not_busy(slug)
        for sub in ("audio", "clips"):
            shutil.rmtree(folder / sub, ignore_errors=True)
        self.cache.clear()
        log.info("Voice %s: working files freed", slug)

    def _not_busy(self, slug: str) -> None:
        if self.jobs.is_busy(slug):
            raise VoiceError(
                "This voice is being cut or built just now. Wait for it to finish.", 409
            )

    # -- audio -----------------------------------------------------------

    def begin_audio(self, slug: str, number: str, name: str) -> Path:
        """Where to stream an upload to, once slug, part and file type check out."""
        folder = self._part(slug, number)
        extension = Path(name or "").suffix.lower()
        if extension not in UPLOAD_TYPES:
            raise VoiceError(
                "That file type is not one the board can read. Use mp3, wav, m4a, flac or ogg.", 415
            )
        (folder / "audio").mkdir(exist_ok=True)
        return folder / "audio" / f"{number}{extension}.partial"

    def finish_audio(self, slug: str, number: str, partial: Path, name: str) -> None:
        """Keep an uploaded recording as the part's audio and queue its cut."""
        folder = self._part(slug, number)
        extension = Path(name).suffix.lower()
        probe = partial.with_name(f"{number}{extension}")
        if workshop.needs_ffmpeg(partial) and not workshop.has_ffmpeg():
            partial.unlink(missing_ok=True)
            raise VoiceError(
                "This board cannot read that kind of file yet because ffmpeg is not "
                "installed. Upload a WAV file instead, or update the board and try again.",
                503,
            )
        audio = folder / "audio"
        while (older := workshop.audio_for(audio, number)) is not None:
            older.unlink(missing_ok=True)
        partial.replace(probe)
        meta = self._meta(folder)
        meta.setdefault("uploads", {})[number] = Path(name).name[:120]
        self._save_meta(folder, meta)
        log.info("Voice %s: recording for part %s saved (%d bytes)", slug, number, _size(probe))
        self.queue_cut(slug, number)

    def remove_audio(self, slug: str, number: str) -> None:
        folder = self._part(slug, number)
        self._not_busy(slug)
        workshop.forget_part(folder, number)
        meta = self._meta(folder)
        (meta.get("uploads") or {}).pop(number, None)
        self._save_meta(folder, meta)
        self.cache.clear()
        log.info("Voice %s: recording for part %s removed", slug, number)

    def queue_cut(self, slug: str, number: str) -> None:
        folder = self._part(slug, number)
        if workshop.audio_for(folder / "audio", number) is None:
            raise VoiceError("There is no recording for this part yet.", 409)
        self.jobs.submit(
            Job(slug, "cut", number, ["cut", "--dir", str(folder.resolve()), "--only", number])
        )

    def queue_build(self, slug: str) -> None:
        folder = self.folder(slug)
        meta = self._meta(folder)
        if not workshop.load_cuts(folder)["clips"]:
            raise VoiceError("Nothing has been cut yet, so there is nothing to build.", 409)
        if not any((folder / "clips").glob("*.wav")):
            raise VoiceError(
                "The working files were cleared. Upload the recordings again to build.", 409
            )
        self.jobs.submit(
            Job(
                slug,
                "build",
                "build",
                [
                    "build",
                    "--dir",
                    str(folder.resolve()),
                    "--name",
                    meta.get("name") or slug,
                    "--out",
                    str((folder / PACK).resolve()),
                ],
            )
        )
        log.info("Voice %s: build queued", slug)

    # -- script ----------------------------------------------------------

    def script_text(self, slug: str, number: str) -> tuple[str, str]:
        folder = self._part(slug, number)
        path = sorted((folder / "script").glob(f"{number}-*.txt"))[0]
        return path.stem, path.read_text(encoding="utf-8")

    def script_zip(self, slug: str) -> bytes:
        folder = self.folder(slug)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((folder / "script").glob("[0-9][0-9]-*.txt")):
                archive.writestr(path.name, path.read_text(encoding="utf-8"))
            index = folder / "script" / "INDEX.md"
            if index.is_file():
                archive.writestr("INDEX.md", index.read_text(encoding="utf-8"))
        return buffer.getvalue()

    def add_extra(self, slug: str, kind: str, wanted: list[dict]) -> None:
        """A redo part for rejected clips, or a top-up for what the board lacked."""
        folder = self.folder(slug)
        self._not_busy(slug)
        options = self._meta(folder).get("options") or {}
        chars, items = PASTE_LIMITS.get(options.get("paste_limit", 5000), PASTE_LIMITS[5000])
        try:
            workshop.write_script(
                folder,
                one_reading=bool(options.get("one_reading")),
                chars=chars,
                items=items,
                topup=wanted if kind == "topup" else None,
                redo=kind == "redo",
            )
        except workshop.NothingToRecord as exc:
            what = (
                "the board has not missed any clips" if kind == "topup" else "no clip is rejected"
            )
            raise VoiceError(f"There is nothing to add: {what}.", 409) from exc
        log.info("Voice %s: %s part added", slug, kind)

    # -- review ----------------------------------------------------------

    def review(self, slug: str) -> workshop.Review:
        return workshop.Review(self.folder(slug), self.cache)

    async def close(self) -> None:
        await self.jobs.close()
        self.cache.clear()


def _station_words(message: str) -> str:
    if message.startswith("Name some stations"):
        return "Choose at least one station to make the script for."
    return message


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
