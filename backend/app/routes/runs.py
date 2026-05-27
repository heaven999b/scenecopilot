from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ..agent import events as event_bus
from ..domain.runtime_models import RunStatus
from ..models import ApprovalDecisionRequest
from ..models import RunDetailResponse
from ..models import RunApprovalResponse
from ..services.approval_service import approval_service
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from ..services.scene_memory_service import scene_memory_service
from ..services.session_manager import session_manager

router = APIRouter(prefix="/api/runs", tags=["runs"])


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

    return RunApprovalResponse(
        run_id=run_id,
        status=next_status.value,
        approval_status=resolved["status"],
        reviewer_note=req.reviewer_note,
    )
