from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..config import DEMO_USER_ID
from ..db import get_conn, row_to_dict
from ..models import StateResponse
from ..services.session_manager import session_manager

router = APIRouter(prefix="/api/state", tags=["state"])


@router.get("", response_model=StateResponse)
async def get_state() -> StateResponse:
    conn = get_conn()
    try:
        documents = conn.execute(
            """
            SELECT id, title, summary, source_path, created_at
            FROM documents
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 8
            """,
            (DEMO_USER_ID,),
        ).fetchall()
        captures = conn.execute(
            """
            SELECT id, session_id, run_id, image_path, prompt, ocr_text, scene_summary, risk_level, decisions_json, created_at
            FROM scene_captures
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 8
            """,
            (DEMO_USER_ID,),
        ).fetchall()
        cards = conn.execute(
            """
            SELECT id, scene_capture_id, run_id, title, detail, priority, status, created_at
            FROM action_cards
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 8
            """,
            (DEMO_USER_ID,),
        ).fetchall()
    finally:
        conn.close()

    recent_runs = await asyncio.to_thread(
        session_manager.list_recent_runs,
        user_id=DEMO_USER_ID,
        limit=8,
    )
    return StateResponse(
        documents=[row_to_dict(row) for row in documents],
        recent_captures=[row_to_dict(row) for row in captures],
        action_cards=[row_to_dict(row) for row in cards],
        recent_runs=recent_runs,
    )
