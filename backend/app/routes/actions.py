from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ..agent import events as event_bus
from ..domain.runtime_models import RunStatus
from ..models import ActionCardExecuteRequest, ActionCardExecuteResponse
from ..runtime import scheduler
from ..services.audit_service import audit_service
from ..services.choice_execution_service import choice_execution_service
from ..services.scene_memory_service import scene_memory_service
from ..services.session_manager import session_manager

router = APIRouter(prefix="/api/action-cards", tags=["action-cards"])


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

    await asyncio.to_thread(
        audit_service.record,
        session_id=run["session_id"],
        run_id=run["id"],
        event_type="action_card_executed",
        detail={
            "card_id": card_id,
            "option_id": req.option_id,
            "status": status,
            "continuation_run_id": result.get("continuation_run_id"),
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
            "continuation_run_id": result.get("continuation_run_id"),
        },
        run_id=run["id"],
    )

    return ActionCardExecuteResponse(
        card_id=card_id,
        run_id=run["id"],
        option_id=req.option_id,
        status=status,
        message=result["message"],
        continuation_run_id=result.get("continuation_run_id"),
        continuation_state=result.get("continuation_state"),
        evidence=result.get("evidence") or {},
    )
