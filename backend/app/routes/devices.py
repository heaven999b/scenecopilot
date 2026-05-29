from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import DeviceListResponse, DeviceRegisterRequest, DeviceRegisterResponse
from ..services.auth_service import auth_service

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.post("/register", response_model=DeviceRegisterResponse)
async def register_device(req: DeviceRegisterRequest, request: Request) -> DeviceRegisterResponse:
    if not auth_service.can_register_device(request=request):
        raise HTTPException(status_code=401, detail="Device registration requires a valid server key.")
    payload = auth_service.register_device(
        display_name=req.display_name,
        platform=req.platform,
        client_version=req.client_version,
    )
    return DeviceRegisterResponse(**payload)


@router.get("", response_model=DeviceListResponse)
async def list_devices() -> DeviceListResponse:
    return DeviceListResponse(items=auth_service.list_devices())
