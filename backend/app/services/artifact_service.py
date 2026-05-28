from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..config import DEMO_USER_ID
from ..db import conn_ctx, get_conn, row_to_dict
from ..domain.runtime_models import ArtifactRecord, ArtifactType


class ArtifactService:
    def record_artifact(
        self,
        *,
        session_id: str,
        run_id: str,
        artifact_type: ArtifactType,
        stage: str,
        provider: str,
        content: dict[str, Any],
        user_id: int = DEMO_USER_ID,
    ) -> int:
        with conn_ctx() as conn:
            cur = conn.execute(
                """
                INSERT INTO run_artifacts
                  (user_id, session_id, run_id, artifact_type, stage, provider, content_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    run_id,
                    artifact_type.value,
                    stage,
                    provider,
                    json.dumps(content, default=str),
                ),
            )
            return int(cur.lastrowid)

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, artifact_type, stage, provider, content_json, created_at
                FROM run_artifacts
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def latest_artifact(self, run_id: str, artifact_type: ArtifactType | str) -> dict[str, Any] | None:
        artifact_value = artifact_type.value if isinstance(artifact_type, ArtifactType) else str(artifact_type)
        conn = get_conn()
        try:
            row = conn.execute(
                """
                SELECT id, artifact_type, stage, provider, content_json, created_at
                FROM run_artifacts
                WHERE run_id = ? AND artifact_type = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, artifact_value),
            ).fetchone()
        finally:
            conn.close()
        return row_to_dict(row) if row is not None else None


artifact_service = ArtifactService()
