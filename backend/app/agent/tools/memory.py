from __future__ import annotations

import json
from typing import Any

from ...config import DEMO_USER_ID
from ...db import conn_ctx, get_conn, row_to_dict


async def save_scene_memory(
    *,
    session_id: str,
    run_id: str | None,
    prompt: str,
    image_path: str | None,
    ocr_text: str,
    scene_summary: str,
    risk_level: str,
    decision: dict[str, Any],
    user_id: int = DEMO_USER_ID,
) -> dict[str, Any]:
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
                risk_level,
                json.dumps([decision], default=str),
            ),
        )
        capture_id = cur.lastrowid

        card_id = None
        if decision.get("title") and decision.get("recommendation"):
            card = conn.execute(
                """
                INSERT INTO action_cards (user_id, scene_capture_id, run_id, title, detail, priority, status)
                VALUES (?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    user_id,
                    capture_id,
                    run_id,
                    decision["title"],
                    decision["recommendation"],
                    decision.get("priority", "medium"),
                ),
            )
            card_id = card.lastrowid

    return {
        "source": "sqlite",
        "scene_capture_id": capture_id,
        "action_card_id": card_id,
    }


async def list_action_cards(limit: int = 5, user_id: int = DEMO_USER_ID) -> dict[str, Any]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, scene_capture_id, run_id, title, detail, priority, status, created_at
            FROM action_cards
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return {
        "source": "sqlite",
        "items": [row_to_dict(row) for row in rows],
    }
