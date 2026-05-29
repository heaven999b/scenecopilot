from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

from ..config import DEMO_USER_ID, PROVIDER_RETRY_COUNT, TOOL_TIMEOUT_SEC
from .audit_service import audit_service

T = TypeVar("T")


@dataclass(slots=True)
class ProviderExecutionResult(Generic[T]):
    value: T | None
    provider_name: str | None
    attempts: list[dict[str, Any]]
    last_error: str
    fallback_used: bool


def _classify_error(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, ValueError):
        return "validation_error"
    if isinstance(exc, (ConnectionError, OSError)):
        return "provider_unavailable"
    lower = f"{type(exc).__name__}: {exc}".lower()
    if "rate limit" in lower or "too many requests" in lower:
        return "rate_limited"
    if "auth" in lower or "api key" in lower or "unauthorized" in lower:
        return "authentication_error"
    return "runtime_error"


def _should_retry(error_type: str) -> bool:
    return error_type in {"timeout", "provider_unavailable", "rate_limited"}


class ProviderRuntimeService:
    async def execute(
        self,
        *,
        stage: str,
        session_id: str,
        run_id: str,
        providers: list[object],
        invoke: Callable[[object], Awaitable[T]],
        validate: Callable[[T], None] | None = None,
        user_id: int = DEMO_USER_ID,
        timeout_sec: float = TOOL_TIMEOUT_SEC,
        retry_count: int = PROVIDER_RETRY_COUNT,
    ) -> ProviderExecutionResult[T]:
        if not providers:
            return ProviderExecutionResult(
                value=None,
                provider_name=None,
                attempts=[],
                last_error="No provider configured.",
                fallback_used=True,
            )

        attempts: list[dict[str, Any]] = []
        last_error = "No provider succeeded."

        for provider in providers:
            provider_name = getattr(provider, "name", provider.__class__.__name__)
            for attempt_index in range(1, retry_count + 2):
                started = time.perf_counter()
                try:
                    value = await asyncio.wait_for(invoke(provider), timeout=timeout_sec)
                    if validate is not None:
                        validate(value)
                    duration_ms = round((time.perf_counter() - started) * 1000, 2)
                    attempt_detail = {
                        "stage": stage,
                        "provider": provider_name,
                        "attempt": attempt_index,
                        "outcome": "success",
                        "duration_ms": duration_ms,
                    }
                    attempts.append(attempt_detail)
                    audit_service.record(
                        session_id=session_id,
                        run_id=run_id,
                        event_type="provider_attempt_completed",
                        detail=attempt_detail,
                        user_id=user_id,
                    )
                    return ProviderExecutionResult(
                        value=value,
                        provider_name=provider_name,
                        attempts=attempts,
                        last_error="",
                        fallback_used=False,
                    )
                except Exception as exc:
                    duration_ms = round((time.perf_counter() - started) * 1000, 2)
                    error_type = _classify_error(exc)
                    last_error = f"{type(exc).__name__}: {exc}"
                    attempt_detail = {
                        "stage": stage,
                        "provider": provider_name,
                        "attempt": attempt_index,
                        "outcome": "failure",
                        "duration_ms": duration_ms,
                        "error_type": error_type,
                        "error": last_error,
                        "retry_scheduled": attempt_index <= retry_count and _should_retry(error_type),
                    }
                    attempts.append(attempt_detail)
                    audit_service.record(
                        session_id=session_id,
                        run_id=run_id,
                        event_type="provider_attempt_completed",
                        detail=attempt_detail,
                        user_id=user_id,
                    )
                    if attempt_index <= retry_count and _should_retry(error_type):
                        continue
                    break

        audit_service.record(
            session_id=session_id,
            run_id=run_id,
            event_type="provider_chain_exhausted",
            detail={
                "stage": stage,
                "attempt_count": len(attempts),
                "last_error": last_error,
            },
            user_id=user_id,
        )
        return ProviderExecutionResult(
            value=None,
            provider_name=None,
            attempts=attempts,
            last_error=last_error,
            fallback_used=True,
        )


provider_runtime_service = ProviderRuntimeService()
