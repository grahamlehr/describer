"""FastAPI application: board, admin page, JSON API and the SSE stream."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.responses import Response
from starlette.types import Scope

from . import credentials, setup
from .announce.clips import MAX_PACK_BYTES
from .announce.cutter import CutError
from .announce.scheduler import TEST_TEXT, AnnouncementScheduler
from .announce.tts import TtsEngine, TtsError
from .announce.voices import MAX_AUDIO_BYTES, MIN_FREE_BYTES, VoiceError, VoiceStore
from .announce.workshop import WorkshopError, has_ffmpeg
from .config import Config, ConfigStore, config_path, load_config
from .kiosk import KioskError, KioskWatchdog
from .profiles import next_change
from .rail.models import Board, Service
from .rail.poller import Poller
from .schedule import is_display_on
from .system import Power, PowerError
from .updater import UpdateError, Updater
from .weather import WeatherService

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web" / "static"

#: The checkout the app runs from, and the one an update moves forward.
REPO_DIR = Path(__file__).resolve().parents[1]

#: Ask the browser to check with us before it reuses anything it has.
#:
#: The board runs for weeks in a kiosk that is never hard-refreshed, and we send
#: no cache-control of our own, so Chromium is free to guess how long a file
#: stays fresh from its age alone. It guessed wrong on the Pi: a newly deployed
#: theme module was drawn against the previous release's stylesheet, which the
#: browser had decided was still good. Revalidating costs a 304 and no transfer,
#: because every response already carries an ETag.
NO_CACHE = {"cache-control": "no-cache"}

#: Heartbeat interval for the SSE stream; keeps proxies and Chromium honest.
SSE_KEEPALIVE = 20.0


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("DESCRIBER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=None,
    )


class TestAnnouncement(BaseModel):
    text: str = TEST_TEXT
    #: Play through this output rather than the saved one: /setup tests the
    #: choice before it is saved.
    audio_device: Literal["hdmi", "jack", "default"] | None = None


class ForceSource(BaseModel):
    """Admin override for the live source. Memory only; never written to YAML."""

    source: Literal["rdm", "rtt", "auto"] = "auto"


class ForceProfile(BaseModel):
    """Admin override for the live profile. Memory only; never written to YAML."""

    profile: str | None = None


class CredentialsUpdate(BaseModel):
    """Keys from /admin. A blank or missing key is left as it is."""

    rdm: str | None = None
    rtt: str | None = None
    clear: list[Literal["rdm", "rtt"]] = []


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    load_dotenv()
    path = config_path()
    store = ConfigStore(load_config(path), path)
    config = store.get()

    engine = TtsEngine(config.announcements)
    announcer = AnnouncementScheduler(engine)
    poller = Poller(store)
    weather = WeatherService(config.weather, store.active)
    weather.on_change = poller.publish_now
    poller.set_weather(weather.forecasts_for_state)
    poller.set_setup(lambda: setup.setup_state(poller, store))

    async def follow_config(_boards: list[Board], active: Config) -> None:
        """Keep Piper on the config actually in force, profile included."""
        engine.update_config(active.announcements)

    poller.add_listener(follow_config)
    poller.add_listener(announcer.on_boards)

    app.state.store = store
    app.state.engine = engine
    app.state.announcer = announcer
    app.state.poller = poller
    app.state.weather = weather
    app.state.voices = VoiceStore(Path(config.announcements.voice_packs_dir))

    updater = Updater(REPO_DIR, config.updates, config_path=path)
    # Open SSE streams never end on their own and would hold the restart up
    # until systemd lost patience; closing them lets it go at once.
    updater.before_restart = poller.close_streams
    app.state.updater = updater
    app.state.power = Power()
    app.state.kiosk = KioskWatchdog()

    await updater.start()
    poller.version = updater.running
    await announcer.start()
    await poller.start()
    await weather.start()
    await app.state.kiosk.start()
    log.info("Describer %s ready; config at %s", updater.running or "(unversioned)", path)
    try:
        yield
    finally:
        await app.state.kiosk.stop()
        await app.state.power.stop()
        await updater.stop()
        await poller.stop()
        await announcer.stop()
        await weather.stop()
        await app.state.voices.close()


class RevalidatedStaticFiles(StaticFiles):
    """Static files the browser has to check with us before reusing them."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["cache-control"] = NO_CACHE["cache-control"]
        return response


app = FastAPI(title="Describer", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def board_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)


@app.get("/admin", include_in_schema=False)
async def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html", headers=NO_CACHE)


@app.get("/setup", include_in_schema=False)
async def setup_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "setup.html", headers=NO_CACHE)


@app.get("/api/state")
async def api_state(request: Request) -> dict:
    return request.app.state.poller.state()


@app.get("/api/config")
async def api_get_config(request: Request) -> dict:
    return request.app.state.store.get().model_dump(mode="json")


@app.put("/api/config")
async def api_put_config(request: Request, payload: dict) -> dict:
    try:
        config = Config.model_validate(payload)
    except ValidationError as exc:
        # A profile's own failure arrives as a ValueError inside ctx, which does
        # not survive JSON; the message already carries what went wrong.
        detail = exc.errors(include_url=False, include_context=False)
        raise HTTPException(status_code=422, detail=detail) from exc

    request.app.state.store.set(config)
    _apply_live(request.app, config)
    return config.model_dump(mode="json")


def _apply_live(app: FastAPI, config: Config) -> None:
    """Put a config that has just been saved to work: no restart for theme,
    stations, sources or announcements."""
    app.state.engine.update_config(app.state.store.active().announcements)
    app.state.updater.update_config(config.updates)
    app.state.weather.update_config(config.weather)
    app.state.weather.config_changed()
    app.state.poller.config_changed()


@app.get("/api/status")
async def api_status(request: Request) -> dict:
    poller: Poller = request.app.state.poller
    store: ConfigStore = request.app.state.store
    config: Config = store.get()
    announcer: AnnouncementScheduler = request.app.state.announcer
    profile = store.active_profile()
    upcoming = next_change(config, forced=store.forced_profile)
    await request.app.state.updater.refresh_deploy()
    return {
        "config_path": str(store.path),
        "updates": request.app.state.updater.status(),
        "system": request.app.state.power.status(),
        "kiosk": request.app.state.kiosk.status(),
        "active_profile": profile.name if profile else None,
        "forced_profile": store.forced_profile,
        "next_profile": upcoming[1] if upcoming else None,
        "profile_changes_at": upcoming[0].isoformat() if upcoming else None,
        **poller.sources.status(),
        "last_fetch": poller.last_fetch.isoformat() if poller.last_fetch else None,
        "last_error": poller.last_error,
        "display_on": is_display_on(config.schedule),
        "display_mode": poller.display_mode,
        "weather": request.app.state.weather.status(),
        "tts": request.app.state.engine.availability(),
        "voice_pack": request.app.state.engine.recorded.status(),
        "last_announcement": announcer.last_spoken,
        "last_announcement_at": (
            announcer.last_spoken_at.isoformat() if announcer.last_spoken_at else None
        ),
        "boards": [
            {
                "crs": board.crs,
                "name": board.name,
                "mode": board.mode,
                "source": board.source,
                "services": len(board.services),
                "stale": board.stale,
                "error": board.error,
                "fetched_at": board.fetched_at.isoformat() if board.fetched_at else None,
            }
            for board in poller.boards()
        ],
    }


@app.post("/api/source/force")
async def api_force_source(request: Request, payload: ForceSource) -> dict:
    """Pin the live source so the fallback can be tested without pulling a cable."""
    poller: Poller = request.app.state.poller
    poller.force_source(None if payload.source == "auto" else payload.source)
    return poller.sources.status()


@app.post("/api/profile/force")
async def api_force_profile(request: Request, payload: ForceProfile) -> dict:
    """Pin a profile so the evening board can be seen at eleven in the morning."""
    store: ConfigStore = request.app.state.store
    name = payload.profile or None
    known = {entry.name for entry in store.get().profiles.entries}
    if name is not None and name not in known:
        raise HTTPException(status_code=404, detail=f"No profile named {name!r}")
    request.app.state.poller.force_profile(name)
    return {"forced_profile": store.forced_profile}


@app.get("/api/credentials")
async def api_get_credentials() -> dict:
    """Whether each key is set and when it changed. Never a value."""
    return credentials.status()


@app.post("/api/credentials")
async def api_set_credentials(request: Request, payload: CredentialsUpdate) -> dict:
    """Write-only: set or clear keys, then put them to work without a restart."""
    updates = {
        source: value
        for source, value in (("rdm", payload.rdm), ("rtt", payload.rtt))
        if value is not None and value.strip()
    }
    clear = set(payload.clear)
    if not updates and not clear:
        raise HTTPException(status_code=422, detail="Nothing to change")
    if set(updates) & clear:
        raise HTTPException(status_code=422, detail="A key cannot be set and cleared at once")
    try:
        credentials.write(updates, clear)
    except credentials.CredentialError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        # The message names the file, never its contents.
        raise HTTPException(status_code=500, detail=f"Could not write the keys: {exc}") from exc

    poller: Poller = request.app.state.poller
    poller.sources.credentials_changed()
    poller.config_changed()
    return credentials.status()


def _validation_error(exc: ValidationError) -> HTTPException:
    return HTTPException(
        status_code=422, detail=exc.errors(include_url=False, include_context=False)
    )


def _key_error(exc: credentials.CredentialError) -> HTTPException:
    # The message names the rule that was broken, never the value.
    return HTTPException(
        status_code=422, detail=[{"loc": ["body", "key"], "msg": str(exc), "type": "value_error"}]
    )


@app.post("/api/setup/test-key")
async def api_setup_test_key(request: Request, payload: setup.KeyCheck) -> dict:
    """One RDM request with a key that is not saved yet. Never retried, never logged."""
    try:
        key = credentials.validate(payload.key)
    except credentials.CredentialError as exc:
        raise _key_error(exc) from exc
    rdm = request.app.state.store.get().sources.rdm
    return await setup.check_key(key, payload.crs, payload.mode, rdm)


@app.post("/api/setup/complete")
async def api_setup_complete(request: Request, payload: setup.SetupComplete) -> dict:
    """Save the wizard's answers: the key, then the config, then put both to work."""
    store: ConfigStore = request.app.state.store
    poller: Poller = request.app.state.poller

    key = (payload.key or "").strip()
    if key:
        try:
            credentials.validate(key)
        except credentials.CredentialError as exc:
            raise _key_error(exc) from exc
    elif not credentials.status()["keys"]["rdm"]["set"]:
        raise _key_error(credentials.CredentialError("Paste your Rail Data Marketplace key"))

    # Validated in full before anything is written, so a bad answer leaves both
    # the key file and config.yaml as they were. The raw config, never active().
    try:
        config = setup.apply_setup(store.get(), payload)
    except ValidationError as exc:
        raise _validation_error(exc) from exc

    if key:
        try:
            credentials.write({"rdm": key})
        except credentials.CredentialError as exc:
            raise _key_error(exc) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not write the key: {exc}") from exc
    try:
        store.set(config)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write the config: {exc}") from exc

    poller.sources.credentials_changed()
    _apply_live(request.app, config)
    # The screen on the TV goes away with this frame, not with the next poll.
    poller.publish_now()
    return {
        "setup": setup.setup_state(poller, store),
        "stations": [station.model_dump(mode="json") for station in config.stations],
    }


@app.get("/api/setup/qr.svg")
async def api_setup_qr() -> Response:
    """The QR code the setup screen shows: the address by number when there is one,
    since every phone can open that; the ``.local`` name otherwise."""
    urls = setup.network_urls()
    return Response(
        setup.qr_svg(urls["ip_url"] or urls["url"]),
        media_type="image/svg+xml",
        headers=NO_CACHE,
    )


@app.get("/api/updates")
async def api_updates(request: Request) -> dict:
    await request.app.state.updater.refresh_deploy()
    return request.app.state.updater.status()


@app.post("/api/updates/check")
async def api_check_updates(request: Request) -> dict:
    """One git fetch, now, rather than at the next interval."""
    try:
        return await request.app.state.updater.check()
    except UpdateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/updates/apply")
async def api_apply_update(request: Request) -> dict:
    """Install what is waiting and restart into it. Never done without this call."""
    try:
        return await request.app.state.updater.update()
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/system/{action}")
async def api_power(request: Request, action: Literal["poweroff", "reboot"]) -> dict:
    """Power the Pi off or reboot it, a second after this answer goes out.

    409 off a Pi, and nothing runs. LAN trust, like the rest of /admin: anyone
    on the network can switch the board off, which is the same trust the six
    presses on the button already assume of anyone in the room.
    """
    power: Power = request.app.state.power
    try:
        power.request(action)
    except PowerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "action": action}


@app.post("/api/kiosk/heartbeat", status_code=204)
async def api_kiosk_heartbeat(request: Request) -> Response:
    """The board page saying its main thread is alive. Not logged: one per 15 s."""
    host = request.client.host if request.client else None
    request.app.state.kiosk.heartbeat(host)
    return Response(status_code=204)


@app.post("/api/kiosk/restart")
async def api_kiosk_restart(request: Request) -> dict:
    """Restart the kiosk browser a second from now. 409 off a Pi."""
    try:
        request.app.state.kiosk.restart_now()
    except KioskError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/announce/test")
async def api_test_announcement(request: Request, payload: TestAnnouncement) -> dict:
    announcer: AnnouncementScheduler = request.app.state.announcer
    try:
        await announcer.say(payload.text, payload.audio_device)
    except TtsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"spoken": payload.text}


# -- voices made on the board -------------------------------------------------

#: Parts of the paste a voice maker takes in one go.
PASTE_LIMITS = (5000, 100000)


class VoiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    near: list[str] = []
    km: float = Field(default=20, ge=0, le=100)
    #: Also record every station on the trains the board is showing now.
    board_stations: bool = False
    all_stations: bool = False
    one_reading: bool = False
    paste_limit: Literal[5000, 100000] = 5000

    @field_validator("near")
    @classmethod
    def _codes(cls, codes: list[str]) -> list[str]:
        cleaned = [code.strip().upper() for code in codes if code.strip()]
        if any(not re.fullmatch(r"[A-Z]{3}", code) for code in cleaned) or len(cleaned) > 20:
            raise ValueError("Stations are given by their three-letter codes, such as PAD")
        return cleaned


class VoiceUse(BaseModel):
    slug: str = Field(default="", max_length=40)


class ReviewChange(BaseModel):
    key: str
    start: int | None = None
    end: int | None = None
    rejected: bool | None = None
    checked: bool | None = None


class VoiceExtra(BaseModel):
    kind: Literal["redo", "topup"]


@app.exception_handler(VoiceError)
async def voice_error_handler(_request: Request, exc: VoiceError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


def _voices(request: Request) -> VoiceStore:
    return request.app.state.voices


def _in_use(request: Request) -> str:
    return request.app.state.store.get().announcements.recorded_voice


def _voice_detail(request: Request, slug: str) -> dict:
    return _voices(request).detail(slug, _in_use(request))


def _voice_overview(request: Request) -> dict:
    store: ConfigStore = request.app.state.store
    config = store.get()
    voices = _voices(request)
    announcements = config.announcements
    # A pack put in place by hand (announcements.voice_pack) still speaks when
    # no made voice is chosen: say so, rather than show Piper as in use.
    legacy = bool(
        not announcements.recorded_voice
        and announcements.use_voice_pack
        and announcements.voice_pack
        and Path(announcements.voice_pack).is_file()
    )
    return {
        "voices": voices.list(announcements.recorded_voice),
        "in_use": announcements.recorded_voice,
        "enabled": announcements.use_voice_pack,
        "legacy_pack": legacy,
        "ffmpeg": has_ffmpeg(),
        "free_bytes": voices.free_bytes(),
        "defaults": {"near": [station.crs for station in config.stations], "km": 20},
    }


def _set_recorded_voice(request: Request, slug: str) -> None:
    """Write the voice in use to the base config and put it to work now."""
    store: ConfigStore = request.app.state.store
    config = store.get()  # the raw file, never active(): this is written back
    changes: dict[str, object] = {"recorded_voice": slug}
    if slug:
        changes["use_voice_pack"] = True
    updated = config.model_copy(
        update={"announcements": config.announcements.model_copy(update=changes)}
    )
    try:
        store.set(updated)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not save the choice of voice: {exc}"
        ) from exc
    _apply_live(request.app, updated)


async def _receive(request: Request, partial: Path, limit: int, voices: VoiceStore) -> int:
    """Stream the request body to ``partial``, refusing what is too big or would fill the disk."""
    declared = int(request.headers.get("content-length") or 0)
    if declared > limit:
        raise HTTPException(status_code=413, detail="That file is too large.")
    free = voices.free_bytes()
    if free - declared < MIN_FREE_BYTES:
        raise HTTPException(
            status_code=507,
            detail="There is not enough free space on the board for that file. "
            "Free some space (remove working files from a voice) and try again.",
        )
    received = 0
    try:
        with partial.open("wb") as sink:
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit:
                    raise HTTPException(status_code=413, detail="That file is too large.")
                if free - received < MIN_FREE_BYTES:
                    raise HTTPException(
                        status_code=507,
                        detail="The board ran out of space for that file. "
                        "Free some space and try again.",
                    )
                sink.write(chunk)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save the file: {exc}") from exc
    return received


@app.get("/api/voices")
async def api_voices(request: Request) -> dict:
    return await asyncio.to_thread(_voice_overview, request)


@app.post("/api/voices", status_code=201)
async def api_create_voice(request: Request, payload: VoiceCreate) -> dict:
    """Name a voice and write its recording script."""
    seen: list[tuple[str, Service]] = []
    if payload.board_stations:
        poller: Poller = request.app.state.poller
        seen = [(board.mode, service) for board in poller.boards() for service in board.services]
    voices = _voices(request)
    slug = await asyncio.to_thread(
        voices.create,
        payload.name,
        near=payload.near,
        km=payload.km,
        seen=seen,
        all_stations=payload.all_stations,
        one_reading=payload.one_reading,
        paste_limit=payload.paste_limit,
        options={"board_stations": payload.board_stations},
    )
    return _voice_detail(request, slug)


@app.post("/api/voices/use")
async def api_use_voice(request: Request, payload: VoiceUse) -> dict:
    """Switch the board to a made voice (or back to Piper with an empty slug)."""
    voices = _voices(request)
    if payload.slug and not voices.pack_path(payload.slug).is_file():
        raise HTTPException(
            status_code=409,
            detail="That voice has not been built yet. Build it first, then switch to it.",
        )
    _set_recorded_voice(request, payload.slug)
    log.info("Voice in use: %s", payload.slug or "(none: Piper)")
    return await asyncio.to_thread(_voice_overview, request)


@app.put("/api/voices/import", status_code=201)
async def api_import_voice(request: Request) -> dict:
    """Keep a ready-made voice pack zip (the request body) as a voice."""
    voices = _voices(request)
    voices.root.mkdir(parents=True, exist_ok=True)
    partial = voices.root / f".import-{uuid.uuid4().hex[:8]}.partial"
    try:
        await _receive(request, partial, MAX_PACK_BYTES, voices)
        slug = await asyncio.to_thread(voices.import_pack, partial)
    finally:
        partial.unlink(missing_ok=True)
    return _voice_detail(request, slug)


@app.get("/api/voices/{slug}")
async def api_voice(request: Request, slug: str) -> dict:
    return await asyncio.to_thread(_voice_detail, request, slug)


@app.delete("/api/voices/{slug}")
async def api_delete_voice(request: Request, slug: str) -> dict:
    voices = _voices(request)
    voices.folder(slug)
    if _in_use(request) == slug:
        _set_recorded_voice(request, "")
    await asyncio.to_thread(voices.delete, slug)
    return await asyncio.to_thread(_voice_overview, request)


@app.get("/api/voices/{slug}/script.zip")
async def api_voice_script_zip(request: Request, slug: str) -> Response:
    body = await asyncio.to_thread(_voices(request).script_zip, slug)
    return Response(
        body,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}-script.zip"'},
    )


@app.get("/api/voices/{slug}/script/{number}")
async def api_voice_script(request: Request, slug: str, number: str) -> Response:
    stem, text = _voices(request).script_text(slug, number)
    return Response(
        text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{stem}.txt"'},
    )


@app.put("/api/voices/{slug}/audio/{number}", status_code=202)
async def api_voice_audio(request: Request, slug: str, number: str, name: str = "") -> dict:
    """One recording per script part: the audio file itself is the request body."""
    voices = _voices(request)
    partial = voices.begin_audio(slug, number, name)
    try:
        received = await _receive(request, partial, MAX_AUDIO_BYTES, voices)
        if received == 0:
            raise HTTPException(status_code=422, detail="The file was empty.")
        voices.finish_audio(slug, number, partial, name)
    finally:
        partial.unlink(missing_ok=True)
    return _voice_detail(request, slug)


@app.delete("/api/voices/{slug}/audio/{number}")
async def api_voice_remove_audio(request: Request, slug: str, number: str) -> dict:
    await asyncio.to_thread(_voices(request).remove_audio, slug, number)
    return _voice_detail(request, slug)


@app.post("/api/voices/{slug}/cut/{number}", status_code=202)
async def api_voice_cut(request: Request, slug: str, number: str) -> dict:
    _voices(request).queue_cut(slug, number)
    return _voice_detail(request, slug)


@app.post("/api/voices/{slug}/build", status_code=202)
async def api_voice_build(request: Request, slug: str) -> dict:
    _voices(request).queue_build(slug)
    return _voice_detail(request, slug)


@app.post("/api/voices/{slug}/extra")
async def api_voice_extra(request: Request, slug: str, payload: VoiceExtra) -> dict:
    wanted = [
        {"key": key, "text": text}
        for key, text in request.app.state.engine.recorded.missing.items()
    ]
    await asyncio.to_thread(_voices(request).add_extra, slug, payload.kind, wanted)
    return _voice_detail(request, slug)


@app.delete("/api/voices/{slug}/working")
async def api_voice_free_working(request: Request, slug: str) -> dict:
    await asyncio.to_thread(_voices(request).free_working, slug)
    return _voice_detail(request, slug)


# The review page asks for these; ``scripts/voicepack.py review`` serves the same
# shapes under the voice "local".


def _review_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="That clip is not in this voice.")
    return HTTPException(status_code=404, detail=str(exc) or "That could not be read.")


@app.get("/api/voices/{slug}/review/cuts")
async def api_voice_review_cuts(request: Request, slug: str) -> dict:
    return await asyncio.to_thread(_voices(request).review(slug).cuts)


@app.get("/api/voices/{slug}/review/clip")
async def api_voice_review_clip(request: Request, slug: str, key: str) -> Response:
    try:
        body = await asyncio.to_thread(_voices(request).review(slug).clip, key)
    except (KeyError, OSError) as exc:
        raise _review_failure(exc) from exc
    return Response(body, media_type="audio/wav", headers=NO_CACHE)


@app.get("/api/voices/{slug}/review/window")
async def api_voice_review_window(
    request: Request, slug: str, part: str, start: int, end: int
) -> Response:
    voices = _voices(request)
    voices.check_part(part)
    try:
        body = await asyncio.to_thread(voices.review(slug).window, part, start, end)
    except (KeyError, OSError, WorkshopError, CutError) as exc:
        raise _review_failure(exc) from exc
    return Response(body, media_type="audio/wav", headers=NO_CACHE)


@app.post("/api/voices/{slug}/review/clip")
async def api_voice_review_update(request: Request, slug: str, payload: ReviewChange) -> dict:
    change = payload.model_dump(exclude={"key"}, exclude_none=True)
    try:
        return await asyncio.to_thread(_voices(request).review(slug).update, payload.key, change)
    except (KeyError, OSError, WorkshopError, CutError) as exc:
        raise _review_failure(exc) from exc


@app.get("/admin/voice-review", include_in_schema=False)
async def voice_review_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "voice-review.html", headers=NO_CACHE)


@app.get("/api/stream")
async def api_stream(request: Request) -> StreamingResponse:
    """Whole-state frames. A disconnect closes this generator, which unsubscribes."""
    poller: Poller = request.app.state.poller
    queue = poller.subscribe()

    async def events() -> AsyncIterator[str]:
        try:
            yield _sse(poller.state())
            while True:
                try:
                    state = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if state is None:
                    return  # Poller.close_streams: we are about to restart
                yield _sse(state)
        finally:
            poller.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _sse(state: dict) -> str:
    return f"data: {json.dumps(state, separators=(',', ':'))}\n\n"
