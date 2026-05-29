from __future__ import annotations

from fastapi import APIRouter

from ..models import ClientIncidentRequest, ClientIncidentResponse
from ..services.client_incident_service import client_incident_service

router = APIRouter(prefix="/api/client", tags=["client"])


@router.post("/incident", response_model=ClientIncidentResponse)
async def record_client_incident(req: ClientIncidentRequest) -> ClientIncidentResponse:
    payload = await client_incident_service.record(
        session_id=req.session_id,
        incident_type=req.incident_type,
        message=req.message,
        run_id=req.run_id,
        details=req.details,
    )
    return ClientIncidentResponse(
        incident_type=payload["incident_type"],
        session_id=req.session_id,
        run_id=req.run_id,
    )
