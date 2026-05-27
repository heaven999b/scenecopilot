from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from ..agent import events as event_bus
from ..config import EVENT_REPLAY_LIMIT

router = APIRouter(prefix="/api/events", tags=["events"])


async def _sse_payload(session_id: str, after_id: int | None, run_id: str | None) -> AsyncIterator[bytes]:
    async for event in event_bus.stream_events(
        session_id,
        run_id=run_id,
        after_id=after_id,
        limit=EVENT_REPLAY_LIMIT,
    ):
        event_type = event.get("event_type", "message")
        body = json.dumps(event, default=str)
        yield f"event: {event_type}\ndata: {body}\n\n".encode("utf-8")


@router.get("/{session_id}")
async def stream_session_events(
    session_id: str,
    run_id: str | None = Query(default=None),
    after_id: int | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    effective_after_id = after_id
    if effective_after_id is None and last_event_id and last_event_id.isdigit():
        effective_after_id = int(last_event_id)
    return StreamingResponse(
        _sse_payload(session_id, effective_after_id, run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
