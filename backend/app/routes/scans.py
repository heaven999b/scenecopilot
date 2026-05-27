from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..agent import core as agent_core
from ..config import DEMO_USER_ID, UPLOADS_DIR
from ..domain.runtime_models import RunStatus
from ..models import ChatResponse
from ..orchestration.planner import build_default_plan
from ..runtime import QueueFullError, scheduler
from ..services.session_manager import session_manager
from ..storage import read_bounded_bytes, write_bytes

router = APIRouter(prefix="/api/scans", tags=["scans"])


@router.post("/analyze", response_model=ChatResponse)
async def analyze_scene(
    image: UploadFile = File(...),
    prompt: str = Form(default="Inspect this scene and tell me what I should do next."),
    session_id: str | None = Form(default=None),
    visible_text: str | None = Form(default=None),
) -> ChatResponse:
    if not image.filename:
        raise HTTPException(status_code=400, detail="Missing image filename")

    suffix = Path(image.filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="Only image uploads are supported here")

    payload = await read_bounded_bytes(image)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOADS_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
    await write_bytes(stored, payload)

    plan = build_default_plan(user_message=prompt, has_image=True)
    handle = await asyncio.to_thread(
        session_manager.start_run,
        user_id=DEMO_USER_ID,
        user_message=prompt,
        session_id=session_id,
        trigger="scan",
        image_count=1,
        input_payload={
            "visible_text_hint": visible_text,
            "image_path": str(stored.resolve()),
        },
        plan=plan,
    )
    try:
        queue_position = await scheduler.submit(
            lambda: agent_core.run_agent(
                user_message=prompt,
                session_id=handle.session_id,
                image_paths=[str(stored.resolve())],
                visible_text=visible_text,
                run_id=handle.run_id,
                trigger="scan",
            ),
            session_id=handle.session_id,
            run_id=handle.run_id,
        )
    except QueueFullError as exc:
        await asyncio.to_thread(
            session_manager.update_run_status,
            handle.run_id,
            status=RunStatus.CANCELLED,
            current_stage="queue_rejected",
            error_message="Run rejected because the queue is full.",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="SceneCopilot is busy. Please retry in a moment.",
        ) from exc
    return ChatResponse(
        session_id=handle.session_id,
        run_id=handle.run_id,
        state="queued",
        queue_position=queue_position,
    )
