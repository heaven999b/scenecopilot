from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..config import DEMO_USER_ID
from ..db import conn_ctx, get_conn, row_to_dict
from ..domain.runtime_models import ApprovalRecord, ApprovalStatus


class ApprovalService:
    def create_record(
        self,
        *,
        session_id: str,
        run_id: str,
        approval: ApprovalRecord,
        user_id: int = DEMO_USER_ID,
    ) -> int:
        with conn_ctx() as conn:
            cur = conn.execute(
                """
                INSERT INTO approval_records
                  (user_id, session_id, run_id, status, risk_level, policy_name, reason, recommended_action, packet_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    run_id,
                    approval.status.value,
                    approval.risk_level.value,
                    approval.policy_name,
                    approval.reason,
                    approval.recommended_action,
                    json.dumps(approval.packet, default=str),
                ),
            )
            return int(cur.lastrowid)

    def list_records(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, status, risk_level, policy_name, reason, recommended_action, packet_json, reviewer_note, resolved_at, created_at
                FROM approval_records
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def resolve_latest_record(
        self,
        run_id: str,
        *,
        approved: bool,
        reviewer_note: str | None = None,
    ) -> dict[str, Any] | None:
        status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        with conn_ctx() as conn:
            latest = conn.execute(
                """
                SELECT id, session_id, status, risk_level, policy_name, reason, recommended_action, packet_json, reviewer_note, resolved_at, created_at
                FROM approval_records
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if latest is None:
                return None
            conn.execute(
                """
                UPDATE approval_records
                SET status = ?, reviewer_note = ?, resolved_at = datetime('now')
                WHERE id = ?
                """,
                (status.value, reviewer_note, latest["id"]),
            )
            row = conn.execute(
                """
                SELECT id, session_id, status, risk_level, policy_name, reason, recommended_action, packet_json, reviewer_note, resolved_at, created_at
                FROM approval_records
                WHERE id = ?
                """,
                (latest["id"],),
            ).fetchone()
        return row_to_dict(row) if row is not None else None


approval_service = ApprovalService()
