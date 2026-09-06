"""FastAPI application: board, admin page, JSON API and the SSE stream."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .announce.scheduler import AnnouncementScheduler
from .announce.tts import TtsEngine, TtsError
from .config import Config, ConfigStore, config_path, load_config
from .rail.poller import Poller
from .schedule import is_display_on

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web" / "static"

#: Heartbeat interval for the SSE stream; keeps proxies and Chromium honest.
SSE_KEEPALIVE = 20.0


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("DESCRIBER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=None,
    )


class TestAnnouncement(BaseModel):
    text: str = "This is a test announcement from the departure board."


class ForceSource(BaseModel):
    """Admin override for the live source. Memory only; never written to YAML."""

    source: Literal["rdm", "rtt", "auto"] = "auto"


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
    poller.add_listener(announcer.on_boards)

    app.state.store = store
    app.state.engine = engine
    app.state.announcer = announcer
    app.state.poller = poller

    await announcer.start()
    await poller.start()
    log.info("Describer ready; config at %s", path)
    try:
        yield
    finally:
        await poller.stop()
        await announcer.stop()


app = FastAPI(title="Describer", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def board_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
async def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


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
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc

    store: ConfigStore = request.app.state.store
    store.set(config)
    # Applied live: no restart for theme, stations, sources or announcements.
    request.app.state.engine.update_config(config.announcements)
    request.app.state.poller.config_changed()
    return config.model_dump(mode="json")


@app.get("/api/status")
async def api_status(request: Request) -> dict:
    poller: Poller = request.app.state.poller
    config: Config = request.app.state.store.get()
    announcer: AnnouncementScheduler = request.app.state.announcer
    return {
        "config_path": str(request.app.state.store.path),
        **poller.sources.status(),
        "last_fetch": poller.last_fetch.isoformat() if poller.last_fetch else None,
        "last_error": poller.last_error,
        "display_on": is_display_on(config.schedule),
        "tts": request.app.state.engine.availability(),
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


@app.post("/api/announce/test")
async def api_test_announcement(request: Request, payload: TestAnnouncement) -> dict:
    announcer: AnnouncementScheduler = request.app.state.announcer
    try:
        await announcer.say(payload.text)
    except TtsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"spoken": payload.text}


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
