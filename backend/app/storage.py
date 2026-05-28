from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from .config import UPLOAD_MAX_BYTES

CHUNK_SIZE = 1024 * 1024


async def copy_upload_to_path(upload: UploadFile, path: Path) -> int:
    total = 0

    def _prepare() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

    await asyncio.to_thread(_prepare)
    while True:
        chunk = await upload.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > UPLOAD_MAX_BYTES:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Upload too large. Limit is {UPLOAD_MAX_BYTES} bytes.",
            )
        await append_bytes(path, chunk)
    return total


async def append_bytes(path: Path, payload: bytes) -> None:
    def _append() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(payload)

    await asyncio.to_thread(_append)
