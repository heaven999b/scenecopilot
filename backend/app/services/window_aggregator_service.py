from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from ..agent import events as event_bus
from ..config import DEMO_USER_ID, SCAN_WINDOW_TTL_SEC
from ..domain.runtime_models import RunStatus
from ..runtime_profiles import RuntimeProfile
from .session_manager import SessionHandle, session_manager

logger = logging.getLogger("scenecopilot.window_aggregator")


@dataclass(slots=True)
class BufferedFrame:
    image_path: str
    captured_at_ms: int | None
    visible_text: str | None
    received_at: float
    size_bytes: int = 0


@dataclass(slots=True)
class ScanWindowState:
    key: str
    session_id: str
    run_id: str
    prompt: str
    capture_profile: str
    runtime_profile: RuntimeProfile
    aggregation_delay_ms: int
    aggregation_max_frames: int
    aggregation_scene_gap_ms: int
    load_tier: str
    created_at: float
    updated_at: float
    flush_deadline_at: float
    frames: list[BufferedFrame] = field(default_factory=list)
    flush_task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class ScanWindowResult:
    session_id: str
    run_id: str
    state: str
    queue_position: int
    frame_count: int
    created_new_window: bool
    coalesced: bool


FlushCallback = Callable[[ScanWindowState, str], Awaitable[int | None]]
CreateRunCallback = Callable[[], Awaitable[SessionHandle]]


class WindowAggregatorService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._windows: dict[str, ScanWindowState] = {}
        self._frames_buffered = 0
        self._coalesced_frames = 0
        self._flushed_windows = 0
        self._immediate_flushes = 0
        self._scene_gap_flushes = 0
        self._adaptive_windows = 0
        self._detached_flushes = 0
        self._expired_windows = 0
        self._background_errors = 0

    def _key_for(self, *, session_id: str, prompt: str, capture_profile: str) -> str:
        normalized_prompt = " ".join(prompt.split()).strip().lower()
        digest = hashlib.sha1(
            f"{session_id}\n{capture_profile}\n{normalized_prompt}".encode("utf-8")
        ).hexdigest()
        return f"{session_id}:{capture_profile}:{digest[:16]}"

    async def register_frame(
        self,
        *,
        session_id: str,
        prompt: str,
        capture_profile: str,
        runtime_profile: RuntimeProfile,
        aggregation_delay_ms: int,
        aggregation_max_frames: int,
        aggregation_scene_gap_ms: int,
        load_tier: str,
        image_path: str,
        captured_at_ms: int | None,
        visible_text: str | None,
        create_run: CreateRunCallback,
        on_flush: FlushCallback,
    ) -> ScanWindowResult:
        now = time.monotonic()
        frame = BufferedFrame(
            image_path=image_path,
            captured_at_ms=captured_at_ms,
            visible_text=visible_text,
            received_at=now,
            size_bytes=self._file_size(image_path),
        )
        key = self._key_for(
            session_id=session_id,
            prompt=prompt,
            capture_profile=capture_profile,
        )

        rollover_flush: tuple[ScanWindowState, asyncio.Task[None] | None] | None = None
        current_flush: tuple[ScanWindowState, asyncio.Task[None] | None] | None = None
        result_window: ScanWindowState | None = None
        frame_count = 1
        created_new_window = False
        coalesced = False
        async with self._lock:
            window = self._windows.get(key)
            if window is not None and self._should_rollover(
                window=window,
                frame=frame,
                aggregation_scene_gap_ms=aggregation_scene_gap_ms,
            ):
                self._windows.pop(key, None)
                flush_task = window.flush_task
                window.flush_task = None
                self._flushed_windows += 1
                self._scene_gap_flushes += 1
                rollover_flush = (window, flush_task)
                window = None
            if window is None:
                handle = await create_run()
                window = ScanWindowState(
                    key=key,
                    session_id=handle.session_id,
                    run_id=handle.run_id,
                    prompt=prompt,
                    capture_profile=capture_profile,
                    runtime_profile=runtime_profile,
                    aggregation_delay_ms=aggregation_delay_ms,
                    aggregation_max_frames=aggregation_max_frames,
                    aggregation_scene_gap_ms=aggregation_scene_gap_ms,
                    load_tier=load_tier,
                    created_at=now,
                    updated_at=now,
                    flush_deadline_at=now + (aggregation_delay_ms / 1000.0),
                    frames=[frame],
                )
                window.flush_task = asyncio.create_task(
                    self._flush_after_deadline(key, run_id=handle.run_id, on_flush=on_flush)
                )
                self._windows[key] = window
                self._frames_buffered += 1
                if load_tier != "steady":
                    self._adaptive_windows += 1
                result_window = window
                created_new_window = True
            else:
                window.frames.append(frame)
                window.updated_at = now
                window.flush_deadline_at = now + (window.aggregation_delay_ms / 1000.0)
                self._frames_buffered += 1
                self._coalesced_frames += 1
                frame_count = len(window.frames)
                result_window = window
                coalesced = True

                if len(window.frames) >= window.aggregation_max_frames:
                    self._windows.pop(key, None)
                    flush_task = window.flush_task
                    window.flush_task = None
                    self._flushed_windows += 1
                    self._immediate_flushes += 1
                    current_flush = (window, flush_task)

        if rollover_flush is not None:
            window, flush_task = rollover_flush
            if flush_task is not None:
                flush_task.cancel()
            self._launch_detached_flush(window, "scene_gap", on_flush)

        if current_flush is not None:
            window, flush_task = current_flush
            if flush_task is not None:
                flush_task.cancel()
            self._launch_detached_flush(window, "max_frames", on_flush)
            return ScanWindowResult(
                session_id=window.session_id,
                run_id=window.run_id,
                state="flushing",
                queue_position=0,
                frame_count=len(window.frames),
                created_new_window=False,
                coalesced=True,
            )

        if result_window is None:
            raise RuntimeError("scan window registration did not produce a window")

        return ScanWindowResult(
            session_id=result_window.session_id,
            run_id=result_window.run_id,
            state="aggregating",
            queue_position=0,
            frame_count=frame_count,
            created_new_window=created_new_window,
            coalesced=coalesced,
        )

    async def _flush_after_deadline(
        self,
        key: str,
        *,
        run_id: str,
        on_flush: FlushCallback,
    ) -> None:
        try:
            while True:
                async with self._lock:
                    window = self._windows.get(key)
                    if window is None or window.run_id != run_id:
                        return
                    remaining = window.flush_deadline_at - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                async with self._lock:
                    window = self._windows.get(key)
                    if window is None or window.run_id != run_id:
                        return
                    remaining = window.flush_deadline_at - time.monotonic()
                    if remaining > 0:
                        continue
                    self._windows.pop(key, None)
                    window.flush_task = None
                    self._flushed_windows += 1
                await self._safe_flush(window, "delay_elapsed", on_flush)
                return
        except asyncio.CancelledError:
            return

    async def _safe_flush(
        self,
        window: ScanWindowState,
        flush_reason: str,
        on_flush: FlushCallback,
    ) -> int | None:
        try:
            return await on_flush(window, flush_reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._background_errors += 1
            logger.exception("scan window flush failed for run %s", window.run_id)
            await asyncio.to_thread(
                session_manager.update_run_status,
                window.run_id,
                status=RunStatus.FAILED,
                current_stage="aggregation_failed",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            await event_bus.emit_event(
                window.session_id,
                "error",
                {"message": f"Buffered scene window failed before launch: {type(exc).__name__}: {exc}"},
                run_id=window.run_id,
                user_id=DEMO_USER_ID,
            )
            await self._cleanup_frame_files(window.frames)
            return None

    async def cleanup_expired(self) -> int:
        cutoff = time.monotonic() - SCAN_WINDOW_TTL_SEC
        expired: list[tuple[ScanWindowState, asyncio.Task[None] | None]] = []
        async with self._lock:
            for key, window in list(self._windows.items()):
                if window.updated_at >= cutoff:
                    continue
                self._windows.pop(key, None)
                expired.append((window, window.flush_task))
                self._expired_windows += 1

        for window, task in expired:
            if task is not None:
                task.cancel()
            await asyncio.to_thread(
                session_manager.update_run_status,
                window.run_id,
                status=RunStatus.CANCELLED,
                current_stage="aggregation_expired",
                error_message="Buffered scene window expired before launch.",
            )
            await event_bus.emit_event(
                window.session_id,
                "error",
                {"message": "Buffered scene window expired before launch and was cancelled."},
                run_id=window.run_id,
                user_id=DEMO_USER_ID,
            )
            await self._cleanup_frame_files(window.frames)
        return len(expired)

    async def reset(self) -> None:
        async with self._lock:
            windows = list(self._windows.values())
            self._windows.clear()
        for window in windows:
            if window.flush_task is not None:
                window.flush_task.cancel()
            await self._cleanup_frame_files(window.frames)

    async def _cleanup_frame_files(self, frames: list[BufferedFrame]) -> None:
        await asyncio.gather(
            *(asyncio.to_thread(Path(frame.image_path).unlink, missing_ok=True) for frame in frames),
            return_exceptions=True,
        )

    def _launch_detached_flush(
        self,
        window: ScanWindowState,
        flush_reason: str,
        on_flush: FlushCallback,
    ) -> None:
        self._detached_flushes += 1
        task = asyncio.create_task(self._safe_flush(window, flush_reason, on_flush))
        task.add_done_callback(lambda _: None)

    @staticmethod
    def _should_rollover(
        *,
        window: ScanWindowState,
        frame: BufferedFrame,
        aggregation_scene_gap_ms: int,
    ) -> bool:
        previous = window.frames[-1] if window.frames else None
        if previous is None:
            return False
        if frame.captured_at_ms is None or previous.captured_at_ms is None:
            return False
        return abs(frame.captured_at_ms - previous.captured_at_ms) > aggregation_scene_gap_ms

    def snapshot(self) -> dict[str, int]:
        pending_frames = sum(len(window.frames) for window in self._windows.values())
        return {
            "pending_windows": len(self._windows),
            "pending_frames": pending_frames,
            "frames_buffered": self._frames_buffered,
            "coalesced_frames": self._coalesced_frames,
            "flushed_windows": self._flushed_windows,
            "immediate_flushes": self._immediate_flushes,
            "scene_gap_flushes": self._scene_gap_flushes,
            "adaptive_windows": self._adaptive_windows,
            "detached_flushes": self._detached_flushes,
            "expired_windows": self._expired_windows,
            "background_errors": self._background_errors,
        }

    @staticmethod
    def _file_size(path: str) -> int:
        try:
            return Path(path).stat().st_size
        except OSError:
            return 0


window_aggregator_service = WindowAggregatorService()
