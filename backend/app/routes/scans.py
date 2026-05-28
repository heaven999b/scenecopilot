from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..agent import core as agent_core
from ..config import DEMO_USER_ID, UPLOADS_DIR
from ..domain.runtime_models import ArtifactType, RunStatus
from ..models import ChatResponse
from ..orchestration.planner import build_default_plan
from ..runtime import QueueFullError, scheduler
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from ..services.media_window_service import media_window_service
from ..services.session_manager import session_manager
from ..storage import read_bounded_bytes, write_bytes

router = APIRouter(prefix="/api/scans", tags=["scans"])


@router.post("/analyze", response_model=ChatResponse)
async def analyze_scene(
    image: UploadFile = File(...),
    prompt: str = Form(default="Inspect this scene and tell me what I should do next."),
    session_id: str | None = Form(default=None),
    visible_text: str | None = Form(default=None),
    captured_at_ms: int | None = Form(default=None),
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
    effective_session_id = session_id or uuid.uuid4().hex[:12]
    aligned_audio_window = await asyncio.to_thread(
        media_window_service.find_best_audio_window,
        effective_session_id,
        target_at_ms=captured_at_ms,
    )
    aligned_audio_paths: list[str] = []
    if aligned_audio_window is not None:
        aligned_audio_path = Path(aligned_audio_window["audio_path"]).resolve()
        if aligned_audio_path.exists():
            aligned_audio_paths.append(str(aligned_audio_path))
        else:
            aligned_audio_window = None

    plan = build_default_plan(
        user_message=prompt,
        has_image=True,
        has_audio=bool(aligned_audio_paths),
    )
    handle = await asyncio.to_thread(
        session_manager.start_run,
        user_id=DEMO_USER_ID,
        user_message=prompt,
        session_id=effective_session_id,
        trigger="scan",
        image_count=1,
        input_payload={
            "visible_text_hint": visible_text,
            "image_path": str(stored.resolve()),
            "captured_at_ms": captured_at_ms,
            "aligned_audio_window": {
                "audio_window_id": aligned_audio_window.get("id"),
                "upload_id": aligned_audio_window.get("upload_id"),
                "run_id": aligned_audio_window.get("run_id"),
                "started_at_ms": aligned_audio_window.get("started_at_ms"),
                "ended_at_ms": aligned_audio_window.get("ended_at_ms"),
                "gap_ms": aligned_audio_window.get("gap_ms"),
                "alignment_mode": aligned_audio_window.get("alignment_mode"),
            } if aligned_audio_window is not None else None,
        },
        plan=plan,
    )
    if aligned_audio_window is not None:
        await asyncio.to_thread(
            artifact_service.record_artifact,
            session_id=handle.session_id,
            run_id=handle.run_id,
            artifact_type=ArtifactType.ALIGNMENT,
            stage="planner",
            provider="session_audio_window_alignment",
            content={
                "captured_at_ms": captured_at_ms,
                "audio_window_id": aligned_audio_window.get("id"),
                "audio_run_id": aligned_audio_window.get("run_id"),
                "started_at_ms": aligned_audio_window.get("started_at_ms"),
                "ended_at_ms": aligned_audio_window.get("ended_at_ms"),
                "gap_ms": aligned_audio_window.get("gap_ms"),
                "alignment_mode": aligned_audio_window.get("alignment_mode"),
            },
            user_id=DEMO_USER_ID,
        )
        await asyncio.to_thread(
            audit_service.record,
            session_id=handle.session_id,
            run_id=handle.run_id,
            event_type="audio_window_aligned",
            detail={
                "captured_at_ms": captured_at_ms,
                "audio_window_id": aligned_audio_window.get("id"),
                "audio_run_id": aligned_audio_window.get("run_id"),
                "gap_ms": aligned_audio_window.get("gap_ms"),
                "alignment_mode": aligned_audio_window.get("alignment_mode"),
            },
            user_id=DEMO_USER_ID,
        )
    try:
        queue_position = await scheduler.submit(
            lambda: agent_core.run_agent(
                user_message=prompt,
                session_id=handle.session_id,
                image_paths=[str(stored.resolve())],
                audio_paths=aligned_audio_paths,
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
