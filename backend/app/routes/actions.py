from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ..agent import core as agent_core
from ..agent import events as event_bus
from ..domain.runtime_models import RunStatus
from ..models import ActionCardExecuteRequest, ActionCardExecuteResponse
from ..runtime import QueueFullError, scheduler
from ..services.audit_service import audit_service
from ..services.choice_execution_service import choice_execution_service
from ..services.scene_memory_service import scene_memory_service
from ..services.session_manager import session_manager

router = APIRouter(prefix="/api/action-cards", tags=["action-cards"])


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


@router.post("/{card_id}/execute", response_model=ActionCardExecuteResponse)
async def execute_action_card(card_id: int, req: ActionCardExecuteRequest) -> ActionCardExecuteResponse:
    try:
        result = await asyncio.to_thread(
            choice_execution_service.execute,
            card_id=card_id,
            option_id=req.option_id,
            note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc)) from exc

    run = result["run"]
    status = result["status"]
    continuation_run_id = result.get("continuation_run_id")
    continuation_queue_position: int | None = None
    if req.option_id == "cancel":
        await asyncio.to_thread(scene_memory_service.update_action_cards_status, run["id"], status="cancelled")
        cancelled_in_scheduler = await scheduler.cancel(run["id"])
        if run["status"] == RunStatus.WAITING_FOR_APPROVAL.value or not cancelled_in_scheduler:
            await asyncio.to_thread(
                session_manager.update_run_status,
                run["id"],
                status=RunStatus.CANCELLED,
                current_stage="cancelled",
                error_message="Run cancelled via action card.",
            )
    if result.get("queue_ready") and continuation_run_id:
        followup_run = await asyncio.to_thread(session_manager.get_run, continuation_run_id)
        payload = result.get("continuation_payload") or {}
        try:
            continuation_queue_position = await _queue_existing_run(
                run=followup_run,
                image_paths=list(payload.get("image_paths") or []),
                audio_paths=list(payload.get("audio_paths") or []),
                visible_text=payload.get("visible_text_hint"),
                trigger=result.get("queue_trigger") or "approval_resume_step",
                prefetched_transcript=payload.get("prefetched_transcript"),
                transcript_source_run_id=payload.get("transcript_source_run_id"),
                prefetched_transcript_source_run_ids=payload.get("prefetched_transcript_source_run_ids"),
            )
        except QueueFullError:
            await asyncio.to_thread(
                session_manager.update_run_status,
                continuation_run_id,
                status=RunStatus.CANCELLED,
                current_stage="queue_rejected",
                error_message="Action-card continuation rejected because the queue is full.",
            )
            continuation_queue_position = None

    await asyncio.to_thread(
        audit_service.record,
        session_id=run["session_id"],
        run_id=run["id"],
        event_type="action_card_executed",
        detail={
            "card_id": card_id,
            "option_id": req.option_id,
            "status": status,
            "continuation_run_id": continuation_run_id,
            "continuation_queue_position": continuation_queue_position,
            "note": req.note,
        },
    )
    await event_bus.emit_event(
        run["session_id"],
        "action_card_executed",
        {
            "card_id": card_id,
            "run_id": run["id"],
            "option_id": req.option_id,
            "status": status,
            "continuation_run_id": continuation_run_id,
            "continuation_queue_position": continuation_queue_position,
        },
        run_id=run["id"],
    )

    return ActionCardExecuteResponse(
        card_id=card_id,
        run_id=run["id"],
        option_id=req.option_id,
        status=status,
        message=result["message"],
        continuation_run_id=continuation_run_id,
        continuation_state=result.get("continuation_state"),
        evidence=result.get("evidence") or {},
    )
