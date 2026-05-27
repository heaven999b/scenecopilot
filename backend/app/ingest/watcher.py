"""Watch a folder for images or text and feed the agent pipeline."""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..config import WATCH_DIR
from . import pipeline

logger = logging.getLogger("scenecopilot.watcher")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
TEXT_EXTS = {".txt", ".md"}
DEBOUNCE_SEC = 1.0

_handled: set[str] = set()
_lock = threading.Lock()


def _mark_once(key: str) -> bool:
    with _lock:
        if key in _handled:
            return False
        _handled.add(key)
        return True


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
        if not _mark_once(str(path.resolve())):
            return

        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            hint_path = path.with_suffix(".txt")
            hint = hint_path.read_text(encoding="utf-8", errors="ignore").strip() if hint_path.exists() else None
            await pipeline.process_image(path, hint=hint)
        elif ext in TEXT_EXTS:
            await pipeline.process_text(path)
        else:
            logger.info("watcher ignored unsupported file: %s", path.name)


def start_watcher(loop: asyncio.AbstractEventLoop) -> Observer:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(_Handler(loop), str(WATCH_DIR), recursive=False)
    observer.start()
    logger.info("watchdog observer started on %s", WATCH_DIR)
    return observer
