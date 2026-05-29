from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..agent import core as agent_core
from ..agent import events as event_bus
from ..config import UPLOADS_DIR
from ..domain.runtime_models import RunStatus
from ..models import ApprovalDecisionRequest
from ..models import RunContinueResponse
from ..models import RunReplayResponse
from ..models import RunDetailResponse
from ..models import RunApprovalResponse
from ..models import RunCancelResponse
from ..models import RunRetryResponse
from ..runtime import QueueFullError, scheduler
from ..services.approval_service import approval_service
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from ..services.continuation_service import continuation_service
from ..services.run_retry_service import run_retry_service
from ..services.scene_memory_service import scene_memory_service
from ..services.session_manager import session_manager
from ..storage import copy_upload_to_path

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _validated_suffix(filename: str | None, *, allowed: set[str]) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in allowed else ""


async def _store_optional_upload(upload: UploadFile | None, *, allowed: set[str], default_ext: str) -> str | None:
    if upload is None:
        return None
    suffix = _validated_suffix(upload.filename, allowed=allowed) or default_ext
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOADS_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
    await copy_upload_to_path(upload, stored)
    return str(stored.resolve())


async def _queue_existing_run(
    *,
    run: dict,
    image_paths: list[str],
    audio_paths: list[str],
    visible_text: str | None,
    trigger: str,
    prefetched_transcript: str | None = None,
    transcript_source_run_id: str | None = None,
    prefetched_transcript_source_run_ids: list[str] | None = None,
) -> int:
    return await scheduler.submit(
        lambda: agent_core.run_agent(
            user_message=run["user_message"],
            session_id=run["session_id"],
            image_paths=image_paths,
            audio_paths=audio_paths,
            prefetched_transcript=prefetched_transcript,
            transcript_source_run_id=transcript_source_run_id,
            prefetched_transcript_source_run_ids=prefetched_transcript_source_run_ids,
            visible_text=visible_text,
            run_id=run["id"],
            trigger=trigger,
        ),
        session_id=run["session_id"],
        run_id=run["id"],
    )


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run(run_id: str) -> RunDetailResponse:
    run, artifacts, approvals, audit_log, scene_captures, action_cards = await asyncio.gather(
        asyncio.to_thread(session_manager.get_run, run_id),
        asyncio.to_thread(artifact_service.list_artifacts, run_id),
        asyncio.to_thread(approval_service.list_records, run_id),
        asyncio.to_thread(audit_service.list_records, run_id),
        asyncio.to_thread(scene_memory_service.list_scene_captures, run_id),
        asyncio.to_thread(scene_memory_service.list_action_cards, run_id),
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunDetailResponse(
        **run,
        artifacts=artifacts,
        approvals=approvals,
        audit_log=audit_log,
        scene_captures=scene_captures,
        action_cards=action_cards,
    )


@router.get("/{run_id}/replay", response_model=RunReplayResponse)
async def replay_run(run_id: str, after_id: int | None = None, limit: int = 120) -> RunReplayResponse:
    run = await asyncio.to_thread(session_manager.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    events = await asyncio.to_thread(
        event_bus.list_persisted_events,
        run["session_id"],
        run_id=run_id,
        after_id=after_id,
        limit=max(1, min(limit, 500)),
    )
    latest_event_id = events[-1]["id"] if events else after_id
    return RunReplayResponse(
        run_id=run_id,
        session_id=run["session_id"],
        status=run["status"],
        current_stage=run.get("current_stage"),
        event_count=len(events),
        latest_event_id=latest_event_id,
        events=events,
        timings_json=run.get("timings_json"),
    )


@router.post("/{run_id}/approve", response_model=RunApprovalResponse)
async def resolve_approval(run_id: str, req: ApprovalDecisionRequest) -> RunApprovalResponse:
    run = await asyncio.to_thread(session_manager.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] != RunStatus.WAITING_FOR_APPROVAL.value:
        raise HTTPException(status_code=409, detail="This run is not awaiting approval.")

    resolved = await asyncio.to_thread(
        approval_service.resolve_latest_record,
        run_id,
        approved=req.decision == "approve",
        reviewer_note=req.reviewer_note,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="No approval record found for this run")

    next_status = RunStatus.COMPLETED if req.decision == "approve" else RunStatus.CANCELLED
    next_stage = "approved" if req.decision == "approve" else "rejected"
    card_status = "approved" if req.decision == "approve" else "rejected"

    await asyncio.to_thread(scene_memory_service.update_action_cards_status, run_id, status=card_status)
    await asyncio.to_thread(
        session_manager.update_run_status,
        run_id,
        status=next_status,
        current_stage=next_stage,
    )
    await asyncio.to_thread(
        audit_service.record,
        session_id=run["session_id"],
        run_id=run_id,
        event_type="approval_resolved",
        detail={
            "decision": req.decision,
            "approval_status": resolved["status"],
            "reviewer_note": req.reviewer_note,
        },
    )
    await event_bus.emit_event(
        run["session_id"],
        "approval_resolved",
        {
            "run_id": run_id,
            "decision": req.decision,
            "approval_status": resolved["status"],
            "reviewer_note": req.reviewer_note,
        },
        run_id=run_id,
    )

    continuation_run_id: str | None = None
    continuation_queue_position: int | None = None
    if req.decision == "approve":
        followup_handle, payload = await asyncio.to_thread(
            continuation_service.start_followup_run,
            source_run=run,
            continuation_reason="approval_resume",
            source_option_id="approve",
            prompt_override=f"Approval granted. Continue with the approved next steps for: {run['user_message']}",
            requires_media=False,
            trigger="approval_resume",
            approval_packet_override=resolved.get("packet_json") if isinstance(resolved.get("packet_json"), dict) else None,
            reviewer_note=req.reviewer_note,
        )
        continuation_run_id = followup_handle.run_id
        followup_run = await asyncio.to_thread(session_manager.get_run, followup_handle.run_id)
        try:
            continuation_queue_position = await _queue_existing_run(
                run=followup_run,
                image_paths=list(payload.get("image_paths") or []),
                audio_paths=list(payload.get("audio_paths") or []),
                visible_text=payload.get("visible_text_hint"),
                trigger="approval_resume",
                prefetched_transcript=payload.get("prefetched_transcript"),
                transcript_source_run_id=payload.get("transcript_source_run_id"),
                prefetched_transcript_source_run_ids=payload.get("prefetched_transcript_source_run_ids"),
            )
        except QueueFullError:
            await asyncio.to_thread(
                session_manager.update_run_status,
                followup_handle.run_id,
                status=RunStatus.CANCELLED,
                current_stage="queue_rejected",
                error_message="Approval continuation rejected because the queue is full.",
            )
            continuation_queue_position = None
        else:
            await asyncio.to_thread(
                audit_service.record,
                session_id=run["session_id"],
                run_id=followup_handle.run_id,
                event_type="approval_continuation_started",
                detail={"source_run_id": run_id},
            )
            await event_bus.emit_event(
                run["session_id"],
                "approval_continuation_started",
                {"source_run_id": run_id, "continuation_run_id": followup_handle.run_id},
                run_id=followup_handle.run_id,
            )

    return RunApprovalResponse(
        run_id=run_id,
        status=next_status.value,
        approval_status=resolved["status"],
        reviewer_note=req.reviewer_note,
        continuation_run_id=continuation_run_id,
        continuation_queue_position=continuation_queue_position,
    )


@router.post("/{run_id}/cancel", response_model=RunCancelResponse)
async def cancel_run(run_id: str) -> RunCancelResponse:
    run = await asyncio.to_thread(session_manager.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] in {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }:
        raise HTTPException(status_code=409, detail="This run is already finished.")

    cancelled_in_scheduler = await scheduler.cancel(run_id)
    if run["status"] == RunStatus.WAITING_FOR_APPROVAL.value or not cancelled_in_scheduler:
        await asyncio.to_thread(scene_memory_service.update_action_cards_status, run_id, status="cancelled")
        await asyncio.to_thread(
            session_manager.update_run_status,
            run_id,
            status=RunStatus.CANCELLED,
            current_stage="cancelled",
            error_message="Run cancelled by operator.",
        )
        await asyncio.to_thread(
            audit_service.record,
            session_id=run["session_id"],
            run_id=run_id,
            event_type="run_cancelled",
            detail={"mode": "direct", "previous_status": run["status"]},
        )
        await event_bus.emit_event(
            run["session_id"],
            "cancelled",
            {"run_id": run_id, "message": "Run cancelled by operator."},
            run_id=run_id,
        )
    else:
        await asyncio.to_thread(scene_memory_service.update_action_cards_status, run_id, status="cancelled")
        await asyncio.to_thread(
            session_manager.update_run_status,
            run_id,
            status=RunStatus.CANCELLED,
            current_stage="cancelled",
            error_message="Run cancelled by operator.",
        )
        await asyncio.to_thread(
            audit_service.record,
            session_id=run["session_id"],
            run_id=run_id,
            event_type="run_cancel_requested",
            detail={"mode": "scheduler", "previous_status": run["status"]},
        )

    return RunCancelResponse(run_id=run_id, status=RunStatus.CANCELLED.value)


@router.post("/{run_id}/retry", response_model=RunRetryResponse)
async def retry_run(run_id: str) -> RunRetryResponse:
    source_run = await asyncio.to_thread(session_manager.get_run, run_id)
    if source_run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    handle, payload = await asyncio.to_thread(run_retry_service.start_retry_run, source_run=source_run)
    image_paths = list(payload.get("image_paths") or [])
    audio_paths = list(payload.get("audio_paths") or [])
    visible_text_hint = payload.get("visible_text_hint")
    prefetched_transcript = payload.get("prefetched_transcript")
    prefetched_transcript_source_run_ids = payload.get("prefetched_transcript_source_run_ids")
    transcript_source_run_id = payload.get("transcript_source_run_id")

    try:
        retry_run_row = await asyncio.to_thread(session_manager.get_run, handle.run_id)
        queue_position = await _queue_existing_run(
            run=retry_run_row,
            image_paths=image_paths,
            audio_paths=audio_paths,
            visible_text=visible_text_hint,
            trigger="retry",
            prefetched_transcript=prefetched_transcript,
            transcript_source_run_id=transcript_source_run_id,
            prefetched_transcript_source_run_ids=prefetched_transcript_source_run_ids,
        )
    except QueueFullError as exc:
        await asyncio.to_thread(
            session_manager.update_run_status,
            handle.run_id,
            status=RunStatus.CANCELLED,
            current_stage="queue_rejected",
            error_message="Retry run rejected because the queue is full.",
        )
        raise HTTPException(
            status_code=429,
            detail="SceneCopilot is busy. Please retry again shortly.",
        ) from exc

    await asyncio.to_thread(
        audit_service.record,
        session_id=handle.session_id,
        run_id=handle.run_id,
        event_type="run_retried",
        detail={
            "source_run_id": source_run["id"],
            "source_status": source_run["status"],
            "missing_image_count": payload.get("missing_image_count", 0),
            "missing_audio": payload.get("missing_audio", False),
        },
    )
    await event_bus.emit_event(
        handle.session_id,
        "run_retried",
        {
            "source_run_id": source_run["id"],
            "retry_run_id": handle.run_id,
            "missing_image_count": payload.get("missing_image_count", 0),
            "missing_audio": payload.get("missing_audio", False),
        },
        run_id=handle.run_id,
    )
    return RunRetryResponse(
        session_id=handle.session_id,
        run_id=handle.run_id,
        source_run_id=source_run["id"],
        state="queued",
        queue_position=queue_position,
    )


@router.post("/{run_id}/continue", response_model=RunContinueResponse)
async def continue_run(
    run_id: str,
    image: UploadFile | None = File(default=None),
    audio: UploadFile | None = File(default=None),
    visible_text: str | None = Form(default=None),
) -> RunContinueResponse:
    run = await asyncio.to_thread(session_manager.get_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] != RunStatus.AWAITING_INPUT.value:
        raise HTTPException(status_code=409, detail="This run is not waiting for additional input.")

    stored_image = await _store_optional_upload(
        image,
        allowed={".jpg", ".jpeg", ".png", ".webp", ".heic"},
        default_ext=".jpg",
    )
    stored_audio = await _store_optional_upload(
        audio,
        allowed={".m4a", ".mp3", ".wav", ".webm", ".ogg", ".mp4", ".mpeg", ".mpga"},
        default_ext=".wav",
    )
    if not stored_image and not stored_audio and not (visible_text or "").strip():
        raise HTTPException(status_code=400, detail="A continuation needs an image, audio clip, or visible text.")

    patch = {
        "image_paths": [stored_image] if stored_image else [],
        "audio_paths": [stored_audio] if stored_audio else [],
        "visible_text_hint": (visible_text or "").strip() or None,
        "required_followup_media": None,
        "continued_from_pending": True,
    }
    await asyncio.to_thread(session_manager.merge_run_input, run_id, patch=patch, image_count=1 if stored_image else 0)
    await asyncio.to_thread(
        session_manager.update_run_status,
        run_id,
        status=RunStatus.QUEUED,
        current_stage="continuation_queued",
    )
    run = await asyncio.to_thread(session_manager.get_run, run_id)
    try:
        queue_position = await _queue_existing_run(
            run=run,
            image_paths=[stored_image] if stored_image else [],
            audio_paths=[stored_audio] if stored_audio else [],
            visible_text=(visible_text or "").strip() or None,
            trigger=run["trigger"],
        )
    except QueueFullError as exc:
        await asyncio.to_thread(
            session_manager.update_run_status,
            run_id,
            status=RunStatus.CANCELLED,
            current_stage="queue_rejected",
            error_message="Continuation rejected because the queue is full.",
        )
        raise HTTPException(status_code=429, detail="SceneCopilot is busy. Please continue again shortly.") from exc

    await asyncio.to_thread(
        audit_service.record,
        session_id=run["session_id"],
        run_id=run_id,
        event_type="run_continued",
        detail={
            "has_image": bool(stored_image),
            "has_audio": bool(stored_audio),
            "has_visible_text": bool((visible_text or "").strip()),
        },
    )
    await event_bus.emit_event(
        run["session_id"],
        "run_continued",
        {
            "run_id": run_id,
            "has_image": bool(stored_image),
            "has_audio": bool(stored_audio),
            "has_visible_text": bool((visible_text or "").strip()),
        },
        run_id=run_id,
    )
    return RunContinueResponse(
        session_id=run["session_id"],
        run_id=run_id,
        state="queued",
        queue_position=queue_position,
    )
