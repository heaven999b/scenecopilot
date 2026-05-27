from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from .config import UPLOAD_MAX_BYTES

CHUNK_SIZE = 1024 * 1024


async def read_bounded_bytes(upload: UploadFile) -> bytes:
    parts: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Upload too large. Limit is {UPLOAD_MAX_BYTES} bytes.",
            )
        parts.append(chunk)
    return b"".join(parts)


async def write_bytes(path: Path, payload: bytes) -> None:
    await asyncio.to_thread(path.write_bytes, payload)


async def append_bytes(path: Path, payload: bytes) -> None:
    def _append() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(payload)

    await asyncio.to_thread(_append)
