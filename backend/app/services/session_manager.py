from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from ..db import conn_ctx, get_conn, row_to_dict
from ..domain.runtime_models import ExecutionPlan, RunStatus


@dataclass(slots=True)
class SessionHandle:
    session_id: str
    run_id: str


def _session_title(user_message: str) -> str:
    text = " ".join(user_message.split()).strip()
    return text[:72] or "SceneCopilot session"


def _preview(user_message: str) -> str:
    return " ".join(user_message.split()).strip()[:160]


class SessionManager:
    """Durable session and run persistence for the SceneCopilot runtime."""

    def start_run(
        self,
        *,
        user_id: int,
        user_message: str,
        session_id: str | None = None,
        trigger: str = "chat",
        image_count: int = 0,
        input_payload: dict[str, Any] | None = None,
        plan: ExecutionPlan | None = None,
    ) -> SessionHandle:
        sid = session_id or uuid.uuid4().hex[:12]
        run_id = uuid.uuid4().hex[:16]
        preview = _preview(user_message)
        title = _session_title(user_message)
        input_json = json.dumps(input_payload or {}, default=str)
        plan_json = json.dumps(asdict(plan), default=str) if plan is not None else "{}"

        with conn_ctx() as conn:
            existing = conn.execute(
                "SELECT id FROM sessions WHERE id = ? AND user_id = ?",
                (sid, user_id),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO sessions (id, user_id, title, status, last_message_preview, last_run_at)
                    VALUES (?, ?, ?, 'active', ?, datetime('now'))
                    """,
                    (sid, user_id, title, preview),
                )
            else:
                conn.execute(
                    """
                    UPDATE sessions
                    SET last_message_preview = ?,
                        last_run_at = datetime('now'),
                        updated_at = datetime('now')
                    WHERE id = ? AND user_id = ?
                    """,
                    (preview, sid, user_id),
                )

            conn.execute(
                """
                INSERT INTO runs
                  (id, user_id, session_id, trigger, status, route_name, user_message, input_json, plan_json, image_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    run_id,
                    user_id,
                    sid,
                    trigger,
                    RunStatus.QUEUED.value,
                    plan.route_name if plan is not None else None,
                    user_message,
                    input_json,
                    plan_json,
                    image_count,
                ),
            )
        return SessionHandle(session_id=sid, run_id=run_id)

    def mark_queued(self, run_id: str, *, queue_position: int) -> None:
        with conn_ctx() as conn:
            conn.execute(
                """
                UPDATE runs
                SET queue_position = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (queue_position, run_id),
            )

    def mark_started(self, run_id: str, *, queue_position: int | None = None) -> None:
        with conn_ctx() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, started_at = COALESCE(started_at, datetime('now')),
                    queue_position = COALESCE(?, queue_position),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (RunStatus.STARTING.value, queue_position, run_id),
            )

    def update_run_status(
        self,
        run_id: str,
        *,
        status: RunStatus,
        current_stage: str | None = None,
        route_name: str | None = None,
        output_text: str | None = None,
        latency_ms: float | None = None,
        error_message: str | None = None,
    ) -> None:
        with conn_ctx() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    current_stage = COALESCE(?, current_stage),
                    route_name = COALESCE(?, route_name),
                    output_text = COALESCE(?, output_text),
                    latency_ms = COALESCE(?, latency_ms),
                    error_message = COALESCE(?, error_message),
                    updated_at = datetime('now'),
                    completed_at = CASE
                      WHEN ? IN (?, ?, ?) THEN datetime('now')
                      ELSE completed_at
                    END
                WHERE id = ?
                """,
                (
                    status.value,
                    current_stage,
                    route_name,
                    output_text,
                    latency_ms,
                    error_message,
                    status.value,
                    RunStatus.COMPLETED.value,
                    RunStatus.FAILED.value,
                    RunStatus.CANCELLED.value,
                    run_id,
                ),
            )

    def merge_run_input(
        self,
        run_id: str,
        *,
        patch: dict[str, Any],
        image_count: int | None = None,
        plan: ExecutionPlan | None = None,
    ) -> None:
        with conn_ctx() as conn:
            row = conn.execute(
                "SELECT input_json FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            current_input: dict[str, Any] = {}
            if row is not None and row["input_json"]:
                try:
                    current_input = json.loads(row["input_json"])
                except json.JSONDecodeError:
                    current_input = {}
            current_input.update(patch)
            plan_json = json.dumps(asdict(plan), default=str) if plan is not None else None
            route_name = plan.route_name if plan is not None else None
            conn.execute(
                """
                UPDATE runs
                SET input_json = ?,
                    image_count = COALESCE(?, image_count),
                    plan_json = COALESCE(?, plan_json),
                    route_name = COALESCE(?, route_name),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    json.dumps(current_input, default=str),
                    image_count,
                    plan_json,
                    route_name,
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = get_conn()
        try:
            row = conn.execute(
                """
                SELECT id, user_id, session_id, trigger, status, route_name, user_message,
                       input_json, plan_json, image_count, queue_position, current_stage,
                       output_text, latency_ms, error_message, created_at, started_at,
                       completed_at, updated_at
                FROM runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        return row_to_dict(row) if row is not None else None

    def list_recent_runs(self, *, user_id: int, limit: int = 8) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, session_id, trigger, status, route_name, user_message,
                       image_count, queue_position, current_stage, latency_ms,
                       error_message, created_at, started_at, completed_at
                FROM runs
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]


session_manager = SessionManager()
