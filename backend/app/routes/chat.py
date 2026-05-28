from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, status

from ..agent import core as agent_core
from ..domain.runtime_models import RunStatus
from ..models import ChatRequest, ChatResponse
from ..orchestration.planner import build_default_plan
from ..runtime import QueueFullError, scheduler
from ..services.session_manager import session_manager
from ..config import DEMO_USER_ID
from ..services.frame_stash_service import frame_stash_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def post_chat(req: ChatRequest) -> ChatResponse:
    image_paths = list(req.image_paths or [])
    stashed_frame_path: str | None = None
    if req.use_latest_frame and not image_paths:
        stashed_frame_path = await frame_stash_service.pop_latest_frame_path(req.session_id)
        if stashed_frame_path:
            image_paths.append(stashed_frame_path)
    image_count = len(image_paths)
    audio_count = len(req.audio_paths or [])
    plan = build_default_plan(
        user_message=req.message,
        has_image=image_count > 0,
        has_audio=audio_count > 0,
    )
    handle = await asyncio.to_thread(
        session_manager.start_run,
        user_id=DEMO_USER_ID,
        user_message=req.message,
        session_id=req.session_id,
        trigger="chat",
        image_count=image_count,
        input_payload={
            "image_paths": image_paths,
            "audio_paths": req.audio_paths or [],
            "use_latest_frame": req.use_latest_frame,
        },
        plan=plan,
    )
    try:
        queue_position = await scheduler.submit(
            lambda: _run_chat_agent(
                req.message,
                handle.session_id,
                image_paths,
                req.audio_paths,
                handle.run_id,
                stashed_frame_path,
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


async def _run_chat_agent(
    message: str,
    session_id: str,
    image_paths: list[str] | None,
    audio_paths: list[str] | None,
    run_id: str,
    stashed_frame_path: str | None,
) -> dict[str, object]:
    try:
        return await agent_core.run_agent(
            user_message=message,
            session_id=session_id,
            image_paths=image_paths,
            audio_paths=audio_paths,
            run_id=run_id,
            trigger="chat",
        )
    finally:
        if stashed_frame_path:
            Path(stashed_frame_path).unlink(missing_ok=True)
