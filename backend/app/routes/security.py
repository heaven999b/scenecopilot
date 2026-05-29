from __future__ import annotations

from fastapi import APIRouter

from ..models import SecurityProfileResponse
from ..services.auth_service import auth_service

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/profile", response_model=SecurityProfileResponse)
async def security_profile() -> SecurityProfileResponse:
    return SecurityProfileResponse(**auth_service.security_profile())
