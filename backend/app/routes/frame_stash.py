from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from ..config import FRAME_STASH_DIR
from ..services.frame_stash_service import frame_stash_service
from ..storage import copy_upload_to_path

router = APIRouter(tags=["frame-stash"])


@router.post("/api/frame/latest")
async def upload_latest_frame(
    image: UploadFile = File(...),
    session_key: str | None = Form(default=None),
    x_scenecopilot_session_key: str | None = Header(default=None),
) -> dict[str, object]:
    sid = (session_key or x_scenecopilot_session_key or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_key required")
    suffix = ".jpg"
    if image.filename:
        candidate = Path(image.filename).suffix.lower()
        if candidate in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
            suffix = candidate
    target = FRAME_STASH_DIR / f"{sid}_latest{suffix}"
    byte_count = await copy_upload_to_path(image, target)
    await frame_stash_service.stash(sid, target)
    return {
        "ok": True,
        "session_key": sid,
        "path": str(target),
        "bytes": byte_count,
    }


@router.get("/api/frame/latest/peek")
async def peek_latest_frame(session_key: str) -> dict[str, object]:
    return await frame_stash_service.peek(session_key)
