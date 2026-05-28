from __future__ import annotations

from typing import Any

from ..config import DEMO_USER_ID
from ..db import conn_ctx, get_conn, row_to_dict


class MediaWindowService:
    def record_audio_window(
        self,
        *,
        session_id: str,
        upload_id: str,
        audio_path: str,
        audio_format: str,
        prompt: str | None = None,
        run_id: str | None = None,
        started_at_ms: int | None = None,
        ended_at_ms: int | None = None,
        user_id: int = DEMO_USER_ID,
    ) -> int:
        duration_ms = None
        if started_at_ms is not None and ended_at_ms is not None:
            duration_ms = max(0, ended_at_ms - started_at_ms)
        with conn_ctx() as conn:
            cur = conn.execute(
                """
                INSERT INTO audio_windows
                  (user_id, session_id, run_id, upload_id, prompt, audio_path, audio_format,
                   started_at_ms, ended_at_ms, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    run_id,
                    upload_id,
                    prompt,
                    audio_path,
                    audio_format,
                    started_at_ms,
                    ended_at_ms,
                    duration_ms,
                ),
            )
            return int(cur.lastrowid)

    def list_recent_audio_windows(self, session_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, session_id, run_id, upload_id, prompt, audio_path, audio_format,
                       started_at_ms, ended_at_ms, duration_ms, created_at
                FROM audio_windows
                WHERE session_id = ?
                ORDER BY COALESCE(ended_at_ms, started_at_ms, 0) DESC, id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def find_best_audio_window(
        self,
        session_id: str,
        *,
        target_at_ms: int | None,
        max_gap_ms: int = 6_000,
    ) -> dict[str, Any] | None:
        windows = self.list_recent_audio_windows(session_id, limit=16)
        if not windows:
            return None
        if target_at_ms is None:
            best = windows[0]
            best["alignment_mode"] = "latest"
            best["gap_ms"] = 0
            return best

        best_window: dict[str, Any] | None = None
        best_gap: int | None = None
        best_mode = "none"
        for window in windows:
            started = window.get("started_at_ms")
            ended = window.get("ended_at_ms")
            if started is None and ended is None:
                continue
            if started is None:
                started = ended
            if ended is None:
                ended = started
            if started <= target_at_ms <= ended:
                gap = 0
                mode = "overlap"
            elif target_at_ms < started:
                gap = started - target_at_ms
                mode = "future_gap"
            else:
                gap = target_at_ms - ended
                mode = "recent_gap"
            if gap > max_gap_ms:
                continue
            if best_gap is None or gap < best_gap:
                best_gap = gap
                best_window = window
                best_mode = mode
        if best_window is None:
            return None
        best_window["alignment_mode"] = best_mode
        best_window["gap_ms"] = best_gap or 0
        return best_window


media_window_service = MediaWindowService()
