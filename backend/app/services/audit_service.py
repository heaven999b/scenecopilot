from __future__ import annotations

import json
from typing import Any

from ..config import DEMO_USER_ID
from ..db import conn_ctx, get_conn, row_to_dict


class AuditService:
    def record(
        self,
        *,
        session_id: str,
        run_id: str,
        event_type: str,
        detail: dict[str, Any],
        user_id: int = DEMO_USER_ID,
    ) -> int:
        with conn_ctx() as conn:
            cur = conn.execute(
                """
                INSERT INTO run_audit_logs
                  (user_id, session_id, run_id, event_type, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    run_id,
                    event_type,
                    json.dumps(detail, default=str),
                ),
            )
            return int(cur.lastrowid)

    def list_records(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, event_type, detail_json, created_at
                FROM run_audit_logs
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]


audit_service = AuditService()
