from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .config import ENABLE_WATCHER
from .db import init_db
from .ingest.watcher import start_watcher
from .routes import chat, dashboard, documents, events, runs, scans, state, system
from .runtime import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scenecopilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    observer = None
    if ENABLE_WATCHER:
        loop = asyncio.get_running_loop()
        observer = start_watcher(loop)
    try:
        yield
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=2.0)
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


for router in (dashboard.router, chat.router, documents.router, events.router, runs.router, scans.router, state.router, system.router):
    app.include_router(router)
