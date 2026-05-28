"""Watch a folder for images or text and feed the agent pipeline."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency path
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except Exception:  # pragma: no cover - import fallback
    FileSystemEvent = Any

    class FileSystemEventHandler:  # type: ignore[override]
        pass

    class Observer:  # type: ignore[override]
        def schedule(self, *args, **kwargs) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            return None

    WATCHDOG_AVAILABLE = False

from ..config import WATCHER_HANDLED_TTL_SEC, WATCH_DIR

logger = logging.getLogger("scenecopilot.watcher")

AUDIO_EXTS = {".wav", ".m4a", ".mp3", ".webm", ".ogg", ".flac"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
TEXT_EXTS = {".txt", ".md"}
DEBOUNCE_SEC = 1.0

_handled: dict[str, float] = {}
_lock = threading.Lock()


def _mark_once(key: str) -> bool:
    now = time.time()
    with _lock:
        _cleanup_locked(now)
        touched_at = _handled.get(key)
        if touched_at is not None and (now - touched_at) < WATCHER_HANDLED_TTL_SEC:
            return False
        _handled[key] = now
        return True


def _cleanup_locked(now: float) -> int:
    removed = 0
    for key, touched_at in list(_handled.items()):
        if (now - touched_at) >= WATCHER_HANDLED_TTL_SEC:
            _handled.pop(key, None)
            removed += 1
    return removed


def cleanup_handled_state() -> int:
    with _lock:
        return _cleanup_locked(time.time())


def snapshot() -> dict[str, int]:
    with _lock:
        _cleanup_locked(time.time())
        return {"handled_entries": len(_handled)}


class _Handler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name.startswith(".") or path.suffix.lower() == ".part":
            return
        future = asyncio.run_coroutine_threadsafe(self._dispatch(path), self.loop)

        def _done(fut):
            exc = fut.exception()
            if exc is not None:
                logger.exception("watcher dispatch failed for %s", path, exc_info=exc)

        future.add_done_callback(_done)

    async def _dispatch(self, path: Path) -> None:
        await asyncio.sleep(DEBOUNCE_SEC)
        if not path.exists():
            return

        ext = path.suffix.lower()
        if ext == ".txt":
            return
        stem_key = str(path.with_suffix("").resolve())
        if not _mark_once(stem_key):
            return

        watched = path.parent
        stem = path.stem
        audio = next(
            (watched / f"{stem}{suffix}" for suffix in AUDIO_EXTS if (watched / f"{stem}{suffix}").exists()),
            None,
        )
        image = next(
            (watched / f"{stem}{suffix}" for suffix in IMAGE_EXTS if (watched / f"{stem}{suffix}").exists()),
            None,
        )
        sidecar = watched / f"{stem}.txt"
        hint = sidecar.read_text(encoding="utf-8", errors="ignore").strip() if sidecar.exists() else None

        try:
            from . import pipeline
            if audio is not None and image is not None:
                await pipeline.process_combined(audio, image)
            elif audio is not None:
                await pipeline.process_audio(audio)
            elif image is not None:
                await pipeline.process_image(image, hint=hint)
            elif ext in TEXT_EXTS:
                await pipeline.process_text(path)
            else:
                logger.info("watcher ignored unsupported file: %s", path.name)
        except Exception:
            with _lock:
                _handled.pop(stem_key, None)
            raise


def start_watcher(loop: asyncio.AbstractEventLoop) -> Observer:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    if not WATCHDOG_AVAILABLE:
        logger.warning("watchdog dependency is unavailable; file watcher is disabled")
        return Observer()
    observer = Observer()
    observer.schedule(_Handler(loop), str(WATCH_DIR), recursive=False)
    observer.start()
    logger.info("watchdog observer started on %s", WATCH_DIR)
    return observer
