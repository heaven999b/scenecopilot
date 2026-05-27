from __future__ import annotations

import json
from typing import Any

from ..config import DEMO_USER_ID
from ..db import conn_ctx, get_conn, row_to_dict
from ..domain.runtime_models import ActionRecommendation
from .audit_service import audit_service


class SceneMemoryService:
    def persist_result(
        self,
        *,
        session_id: str,
        run_id: str,
        prompt: str,
        image_path: str | None,
        ocr_text: str,
        scene_summary: str,
        decision: ActionRecommendation,
        user_id: int = DEMO_USER_ID,
    ) -> dict[str, int | None]:
        payload = {
            "title": decision.title,
            "recommendation": decision.recommendation,
            "risk_level": decision.risk_level.value,
            "next_steps": decision.next_steps,
            "confidence": decision.confidence,
            "priority": decision.priority,
            "blocked": decision.blocked,
            "approval_required": decision.approval_required,
        }
        with conn_ctx() as conn:
            cur = conn.execute(
                """
                INSERT INTO scene_captures
                  (user_id, session_id, run_id, image_path, prompt, ocr_text, scene_summary, risk_level, decisions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    run_id,
                    image_path,
                    prompt,
                    ocr_text,
                    scene_summary,
                    decision.risk_level.value,
                    json.dumps([payload], default=str),
                ),
            )
            capture_id = int(cur.lastrowid)
            card_id: int | None = None
            if decision.title and decision.recommendation:
                card = conn.execute(
                    """
                    INSERT INTO action_cards (user_id, scene_capture_id, run_id, title, detail, priority, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (
                        user_id,
                        capture_id,
                        run_id,
                        decision.title,
                        decision.recommendation,
                        decision.priority,
                    ),
                )
                card_id = int(card.lastrowid)
        audit_service.record(
            session_id=session_id,
            run_id=run_id,
            event_type="scene_memory_persisted",
            detail={"scene_capture_id": capture_id, "action_card_id": card_id},
            user_id=user_id,
        )
        return {"scene_capture_id": capture_id, "action_card_id": card_id}

    def list_scene_captures(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, image_path, prompt, ocr_text, scene_summary, risk_level, decisions_json, created_at
                FROM scene_captures
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def list_action_cards(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, scene_capture_id, title, detail, priority, status, created_at
                FROM action_cards
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def update_action_cards_status(self, run_id: str, *, status: str) -> None:
        with conn_ctx() as conn:
            conn.execute(
                """
                UPDATE action_cards
                SET status = ?
                WHERE run_id = ?
                """,
                (status, run_id),
            )


scene_memory_service = SceneMemoryService()
