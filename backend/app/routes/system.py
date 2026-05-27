from __future__ import annotations

from fastapi import APIRouter

from ..agent import events as event_bus
from ..models import SystemMetricsResponse
from ..runtime import scheduler

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/metrics", response_model=SystemMetricsResponse)
async def get_metrics() -> SystemMetricsResponse:
    return SystemMetricsResponse(
        scheduler=await scheduler.snapshot(),
        event_bus=event_bus.snapshot(),
    )
