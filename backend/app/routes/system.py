from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..agent import events as event_bus
from ..ingest import watcher
from ..models import SystemMetricsResponse
from ..runtime import scheduler
from ..services.frame_stash_service import frame_stash_service
from ..services.media_lifecycle_service import media_lifecycle_service
from ..services.window_aggregator_service import window_aggregator_service

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/metrics", response_model=SystemMetricsResponse)
async def get_metrics() -> SystemMetricsResponse:
    return SystemMetricsResponse(
        scheduler=await scheduler.snapshot(),
        event_bus=event_bus.snapshot(),
        frame_stash=frame_stash_service.snapshot(),
        watcher=watcher.snapshot(),
        scan_aggregator=window_aggregator_service.snapshot(),
        media_lifecycle=await asyncio.to_thread(media_lifecycle_service.snapshot),
    )
