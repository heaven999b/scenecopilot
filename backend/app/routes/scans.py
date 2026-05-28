from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..agent import core as agent_core
from ..config import (
    ALIGNMENT_FUTURE_TOLERANCE_MS,
    ALIGNMENT_MAX_AUDIO_WINDOWS,
    ALIGNMENT_WINDOW_MS,
    DEMO_USER_ID,
    UPLOADS_DIR,
)
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


def _usable_transcript(text: str) -> bool:
    normalized = " ".join(text.split()).strip().lower()
    if not normalized:
        return False
    if normalized.startswith("no speech provider configured"):
        return False
    if "sidecar transcript" in normalized:
        return False
    return True


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


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
    interval_start_ms = captured_at_ms - ALIGNMENT_WINDOW_MS if captured_at_ms is not None else None
    interval_end_ms = captured_at_ms + ALIGNMENT_FUTURE_TOLERANCE_MS if captured_at_ms is not None else None
    aligned_audio_windows = await asyncio.to_thread(
        media_window_service.list_audio_windows_for_interval,
        effective_session_id,
        interval_start_ms=interval_start_ms,
        interval_end_ms=interval_end_ms,
        max_gap_ms=ALIGNMENT_WINDOW_MS,
        limit=ALIGNMENT_MAX_AUDIO_WINDOWS,
    )
    aligned_audio_paths: list[str] = []
    aligned_audio_window_records: list[dict[str, object]] = []
    prefetched_transcripts: list[str] = []
    transcript_source_run_ids: list[str] = []
    for aligned_audio_window in aligned_audio_windows:
        aligned_audio_path = Path(aligned_audio_window["audio_path"]).resolve()
        if not aligned_audio_path.exists():
            continue
        aligned_audio_paths.append(str(aligned_audio_path))
        run_id = str(aligned_audio_window.get("run_id") or "").strip()
        transcript_reused = False
        transcript_artifact_id: int | None = None
        if run_id:
            transcript_artifact = await asyncio.to_thread(
                artifact_service.latest_artifact,
                run_id,
                ArtifactType.TRANSCRIPT,
            )
            if transcript_artifact is not None and transcript_artifact.get("provider") != "fallback":
                transcript_artifact_id = int(transcript_artifact["id"])
                transcript_content = transcript_artifact.get("content_json") or {}
                transcript_text = str(transcript_content.get("transcript") or "").strip()
                if _usable_transcript(transcript_text):
                    prefetched_transcripts.append(transcript_text)
                    transcript_source_run_ids.append(run_id)
                    transcript_reused = True
        aligned_audio_window_records.append({
            "audio_window_id": aligned_audio_window.get("id"),
            "upload_id": aligned_audio_window.get("upload_id"),
            "run_id": aligned_audio_window.get("run_id"),
            "started_at_ms": aligned_audio_window.get("started_at_ms"),
            "ended_at_ms": aligned_audio_window.get("ended_at_ms"),
            "gap_ms": aligned_audio_window.get("gap_ms"),
            "overlap_ms": aligned_audio_window.get("overlap_ms"),
            "alignment_mode": aligned_audio_window.get("alignment_mode"),
            "transcript_reused": transcript_reused,
            "transcript_artifact_id": transcript_artifact_id,
        })
    transcript_source_run_ids = _dedupe_preserve_order(transcript_source_run_ids)
    prefetched_transcript = "\n".join(_dedupe_preserve_order(prefetched_transcripts))

    plan = build_default_plan(
        user_message=prompt,
        has_image=True,
        has_audio=bool(aligned_audio_paths or prefetched_transcript),
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
            "aligned_audio_windows": aligned_audio_window_records,
            "alignment_window_ms": ALIGNMENT_WINDOW_MS,
            "alignment_future_tolerance_ms": ALIGNMENT_FUTURE_TOLERANCE_MS,
            "transcript_reused_count": len(transcript_source_run_ids),
        },
        plan=plan,
    )
    if aligned_audio_window_records:
        await asyncio.to_thread(
            artifact_service.record_artifact,
            session_id=handle.session_id,
            run_id=handle.run_id,
            artifact_type=ArtifactType.ALIGNMENT,
            stage="planner",
            provider="session_audio_multimodal_window",
            content={
                "captured_at_ms": captured_at_ms,
                "window_start_ms": interval_start_ms,
                "window_end_ms": interval_end_ms,
                "audio_window_count": len(aligned_audio_window_records),
                "audio_windows": aligned_audio_window_records,
                "transcript_reused_count": len(transcript_source_run_ids),
                "transcript_source_run_ids": transcript_source_run_ids,
            },
            user_id=DEMO_USER_ID,
        )
        await asyncio.to_thread(
            audit_service.record,
            session_id=handle.session_id,
            run_id=handle.run_id,
            event_type="multimodal_window_aligned",
            detail={
                "captured_at_ms": captured_at_ms,
                "window_start_ms": interval_start_ms,
                "window_end_ms": interval_end_ms,
                "audio_window_count": len(aligned_audio_window_records),
                "transcript_reused_count": len(transcript_source_run_ids),
                "transcript_source_run_ids": transcript_source_run_ids,
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
                prefetched_transcript=prefetched_transcript or None,
                transcript_source_run_id=transcript_source_run_ids[0] if transcript_source_run_ids else None,
                prefetched_transcript_source_run_ids=transcript_source_run_ids or None,
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
