from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .agent import events as event_bus
from .config import DEMO_USER_ID, MAX_CONCURRENT_RUNS, MAX_PENDING_RUNS
from .domain.runtime_models import RunStatus
from .services.session_manager import session_manager


class QueueFullError(RuntimeError):
    """Raised when the execution queue is full."""


class RunScheduler:
    def __init__(self, max_concurrent: int, max_pending: int) -> None:
        self.max_concurrent = max(1, max_concurrent)
        self.max_pending = max(1, max_pending)
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._task_index: dict[str, asyncio.Task[Any]] = {}
        self._pending = 0
        self._active = 0
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._rejected = 0
        self._cancelled = 0

    async def submit(
        self,
        job_factory: Callable[[], Awaitable[Any]],
        *,
        session_id: str,
        run_id: str,
        user_id: int = DEMO_USER_ID,
    ) -> int:
        enqueued_at = time.perf_counter()
        async with self._lock:
            if self._pending >= self.max_pending:
                self._rejected += 1
                raise QueueFullError("run queue is full")
            queue_position = self._pending
            self._pending += 1
            self._submitted += 1
            active_runs = self._active

        await event_bus.emit_event(
            session_id,
            "queued",
            {
                "queue_position": queue_position,
                "active_runs": active_runs,
                "max_concurrent_runs": self.max_concurrent,
                "max_pending_runs": self.max_pending,
            },
            run_id=run_id,
            user_id=user_id,
        )
        await asyncio.to_thread(session_manager.mark_queued, run_id, queue_position=queue_position)

        task = asyncio.create_task(
            self._run_reserved(
                job_factory,
                session_id=session_id,
                run_id=run_id,
                user_id=user_id,
                enqueued_at=enqueued_at,
            )
        )
        self._tasks.add(task)
        self._task_index[run_id] = task
        task.add_done_callback(lambda finished: self._cleanup_task(run_id, finished))
        return queue_position

    def _cleanup_task(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        self._task_index.pop(run_id, None)

    async def _run_reserved(
        self,
        job_factory: Callable[[], Awaitable[Any]],
        *,
        session_id: str,
        run_id: str,
        user_id: int,
        enqueued_at: float,
    ) -> None:
        pending_reserved = True
        active_reserved = False
        try:
            async with self._semaphore:
                queue_wait_ms = round((time.perf_counter() - enqueued_at) * 1000, 2)
                async with self._lock:
                    self._pending -= 1
                    pending_reserved = False
                    self._active += 1
                    active_reserved = True
                    active_runs = self._active
                    pending_runs = self._pending

                await asyncio.to_thread(session_manager.mark_started, run_id, queue_position=None)
                await event_bus.emit_event(
                    session_id,
                    "run_started",
                    {
                        "run_id": run_id,
                        "queue_wait_ms": queue_wait_ms,
                        "active_runs": active_runs,
                        "pending_runs": pending_runs,
                    },
                    run_id=run_id,
                    user_id=user_id,
                )

                try:
                    await job_factory()
                except Exception as exc:
                    async with self._lock:
                        self._failed += 1
                    await asyncio.to_thread(
                        session_manager.update_run_status,
                        run_id,
                        status=RunStatus.FAILED,
                        current_stage="runtime_error",
                        error_message=f"{type(exc).__name__}: {exc}",
                    )
                    await event_bus.emit_event(
                        session_id,
                        "error",
                        {"message": f"{type(exc).__name__}: {exc}"},
                        run_id=run_id,
                        user_id=user_id,
                    )
                else:
                    async with self._lock:
                        self._completed += 1
        except asyncio.CancelledError:
            async with self._lock:
                self._cancelled += 1
                if pending_reserved and self._pending > 0:
                    self._pending -= 1
                if active_reserved and self._active > 0:
                    self._active -= 1
                    active_reserved = False
            await asyncio.to_thread(
                session_manager.update_run_status,
                run_id,
                status=RunStatus.CANCELLED,
                current_stage="cancelled",
                error_message="Run cancelled by operator.",
            )
            await event_bus.emit_event(
                session_id,
                "cancelled",
                {"run_id": run_id, "message": "Run cancelled by operator."},
                run_id=run_id,
                user_id=user_id,
            )
            raise
        finally:
            if active_reserved:
                async with self._lock:
                    self._active -= 1

    async def cancel(self, run_id: str) -> bool:
        async with self._lock:
            task = self._task_index.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self, timeout_sec: float = 5.0) -> None:
        if not self._tasks:
            return
        done, pending = await asyncio.wait(self._tasks, timeout=timeout_sec)
        for task in pending:
            task.cancel()

    async def snapshot(self) -> dict[str, int]:
        async with self._lock:
            return {
                "max_concurrent_runs": self.max_concurrent,
                "max_pending_runs": self.max_pending,
                "pending_runs": self._pending,
                "active_runs": self._active,
                "submitted_runs": self._submitted,
                "completed_runs": self._completed,
                "failed_runs": self._failed,
                "rejected_runs": self._rejected,
                "cancelled_runs": self._cancelled,
                "live_tasks": len(self._tasks),
                "cancellable_runs": len(self._task_index),
            }


scheduler = RunScheduler(MAX_CONCURRENT_RUNS, MAX_PENDING_RUNS)
