from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import FRAME_STASH_DIR, FRAME_STASH_TTL_SEC


@dataclass
class _FrameEntry:
    path: Path
    updated_at: float


class FrameStashService:
    def __init__(self) -> None:
        self._latest: dict[str, _FrameEntry] = {}
        self._lock = asyncio.Lock()
        FRAME_STASH_DIR.mkdir(parents=True, exist_ok=True)

    async def stash(self, session_key: str, path: Path) -> None:
        previous: Path | None = None
        async with self._lock:
            prior = self._latest.get(session_key)
            if prior is not None and prior.path != path:
                previous = prior.path
            self._latest[session_key] = _FrameEntry(path=path, updated_at=time.time())
        if previous is not None and previous.exists():
            previous.unlink(missing_ok=True)

    async def pop_latest_frame_path(self, session_key: str | None) -> str | None:
        if not session_key:
            return None
        async with self._lock:
            entry = self._latest.pop(session_key, None)
        if entry is None:
            return None
        return str(entry.path) if entry.path.exists() else None

    async def peek(self, session_key: str | None) -> dict[str, object]:
        if not session_key:
            return {"session_key": session_key, "has_pending": False, "path": None}
        async with self._lock:
            entry = self._latest.get(session_key)
        return {
            "session_key": session_key,
            "has_pending": bool(entry and entry.path.exists()),
            "path": str(entry.path) if entry else None,
        }

    async def cleanup_expired(self) -> int:
        now = time.time()
        expired: list[tuple[str, Path]] = []
        async with self._lock:
            for session_key, entry in list(self._latest.items()):
                if (now - entry.updated_at) >= FRAME_STASH_TTL_SEC or not entry.path.exists():
                    expired.append((session_key, entry.path))
                    self._latest.pop(session_key, None)
        removed = 0
        for _, path in expired:
            if path.exists():
                path.unlink(missing_ok=True)
            removed += 1
        return removed

    async def reset(self) -> None:
        async with self._lock:
            entries = list(self._latest.values())
            self._latest.clear()
        for entry in entries:
            if entry.path.exists():
                entry.path.unlink(missing_ok=True)

    def snapshot(self) -> dict[str, int]:
        now = time.time()
        pending = 0
        expired = 0
        for entry in self._latest.values():
            if (now - entry.updated_at) >= FRAME_STASH_TTL_SEC:
                expired += 1
            else:
                pending += 1
        return {
            "pending_frames": pending,
            "expired_frames": expired,
        }


frame_stash_service = FrameStashService()
