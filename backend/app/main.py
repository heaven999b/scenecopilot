from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .agent import events as event_bus
from .config import ENABLE_WATCHER, HOUSEKEEPING_INTERVAL_SEC
from .db import init_db
from .ingest import watcher
from .ingest.watcher import start_watcher
from .routes import audio, chat, dashboard, documents, events, frame_stash, runs, scans, state, system
from .runtime import scheduler
from .services.frame_stash_service import frame_stash_service
from .services.window_aggregator_service import window_aggregator_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scenecopilot")


async def _housekeeping_loop() -> None:
    while True:
        try:
            await asyncio.sleep(HOUSEKEEPING_INTERVAL_SEC)
            await frame_stash_service.cleanup_expired()
            await event_bus.cleanup_stale_state()
            watcher.cleanup_handled_state()
            await window_aggregator_service.cleanup_expired()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("housekeeping loop failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    observer = None
    housekeeping_task = asyncio.create_task(_housekeeping_loop())
    if ENABLE_WATCHER:
        loop = asyncio.get_running_loop()
        observer = start_watcher(loop)
    try:
        yield
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=2.0)
        housekeeping_task.cancel()
        try:
            await housekeeping_task
        except asyncio.CancelledError:
            pass
        await frame_stash_service.reset()
        await window_aggregator_service.reset()
        await scheduler.shutdown()


app = FastAPI(title="SceneCopilot", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_timing(request: Request, call_next):
    started = time.perf_counter()
    response: Response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return response


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "scheduler": await scheduler.snapshot(),
    }


for router in (
    dashboard.router,
    chat.router,
    audio.router,
    documents.router,
    events.router,
    frame_stash.router,
    runs.router,
    scans.router,
    state.router,
    system.router,
):
    app.include_router(router)
