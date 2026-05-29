from __future__ import annotations

import asyncio
from typing import Any

from ..agent import events as event_bus
from ..config import DEMO_USER_ID
from .audit_service import audit_service
from .session_manager import session_manager


class ClientIncidentService:
    async def record(
        self,
        *,
        session_id: str,
        incident_type: str,
        message: str | None = None,
        run_id: str | None = None,
        details: dict[str, Any] | None = None,
        user_id: int = DEMO_USER_ID,
    ) -> dict[str, Any]:
        normalized_type = incident_type.strip().lower()
        payload = {
            "incident_type": normalized_type,
            "message": (message or "").strip() or None,
            "details": details or {},
        }
        await event_bus.emit_event(
            session_id,
            "client_incident",
            payload,
            run_id=run_id,
            user_id=user_id,
        )
        if run_id:
            await asyncio.to_thread(
                audit_service.record,
                session_id=session_id,
                run_id=run_id,
                event_type="client_incident",
                detail=payload,
                user_id=user_id,
            )
            patch = {"last_client_incident": payload}
            await asyncio.to_thread(session_manager.merge_run_input, run_id, patch=patch)
        return payload


client_incident_service = ClientIncidentService()
