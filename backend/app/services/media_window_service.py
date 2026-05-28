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

    def list_audio_windows_for_interval(
        self,
        session_id: str,
        *,
        interval_start_ms: int | None,
        interval_end_ms: int | None,
        max_gap_ms: int = 6_000,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        windows = self.list_recent_audio_windows(session_id, limit=max(16, limit * 4))
        if not windows:
            return []
        if interval_start_ms is None and interval_end_ms is None:
            return windows[:limit]

        if interval_start_ms is None:
            interval_start_ms = interval_end_ms
        if interval_end_ms is None:
            interval_end_ms = interval_start_ms
        if interval_start_ms is None or interval_end_ms is None:
            return []

        if interval_start_ms > interval_end_ms:
            interval_start_ms, interval_end_ms = interval_end_ms, interval_start_ms

        candidates: list[dict[str, Any]] = []
        for raw_window in windows:
            started = raw_window.get("started_at_ms")
            ended = raw_window.get("ended_at_ms")
            if started is None and ended is None:
                continue
            if started is None:
                started = ended
            if ended is None:
                ended = started
            if started is None or ended is None:
                continue

            overlap_ms = min(ended, interval_end_ms) - max(started, interval_start_ms)
            if overlap_ms >= 0:
                gap_ms = 0
                alignment_mode = "window_overlap"
            elif ended < interval_start_ms:
                gap_ms = interval_start_ms - ended
                alignment_mode = "recent_gap"
            else:
                gap_ms = started - interval_end_ms
                alignment_mode = "future_gap"
            if gap_ms > max_gap_ms:
                continue

            window = dict(raw_window)
            window["alignment_mode"] = alignment_mode
            window["gap_ms"] = gap_ms
            window["overlap_ms"] = max(0, overlap_ms)
            candidates.append(window)

        candidates.sort(
            key=lambda item: (
                int(item.get("gap_ms", 0)),
                -int(item.get("overlap_ms", 0)),
                int(item.get("started_at_ms") or item.get("ended_at_ms") or 0),
            )
        )
        selected = candidates[:limit]
        selected.sort(key=lambda item: int(item.get("started_at_ms") or item.get("ended_at_ms") or 0))
        return selected

    def find_best_audio_window(
        self,
        session_id: str,
        *,
        target_at_ms: int | None,
        max_gap_ms: int = 6_000,
    ) -> dict[str, Any] | None:
        aligned = self.list_audio_windows_for_interval(
            session_id,
            interval_start_ms=target_at_ms,
            interval_end_ms=target_at_ms,
            max_gap_ms=max_gap_ms,
            limit=1,
        )
        if not aligned:
            return None
        best_window = aligned[0]
        if target_at_ms is None:
            best_window["alignment_mode"] = "latest"
            best_window["gap_ms"] = 0
        return best_window


media_window_service = MediaWindowService()
