"""Session-scoped event bus persisted in SQLite and streamed via SSE."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator

from ..config import DEMO_USER_ID
from ..db import get_conn

_SUBSCRIBERS: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
_LOCK = asyncio.Lock()


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def _get_subscribers(session_id: str) -> list[asyncio.Queue[dict[str, Any]]]:
    queues = _SUBSCRIBERS.get(session_id)
    if queues is None:
        queues = []
        _SUBSCRIBERS[session_id] = queues
    return queues


async def emit_event(
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
    user_id: int = DEMO_USER_ID,
) -> dict[str, Any]:
    event = {
        "session_id": session_id,
        "run_id": run_id,
        "event_type": event_type,
        "payload": payload,
        "ts": time.time(),
    }

    conn = get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO reasoning_events (user_id, session_id, run_id, event_type, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, session_id, run_id, event_type, json.dumps(payload, default=str)),
        )
        conn.commit()
        event["id"] = cur.lastrowid
    finally:
        conn.close()

    async with _LOCK:
        subscribers = list(_get_subscribers(session_id))

    for queue in subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            queue.put_nowait(event)

    return event


async def stream_events(
    session_id: str,
    *,
    run_id: str | None = None,
    after_id: int | None = None,
    limit: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    conn = get_conn()
    try:
        if after_id is None:
            if run_id is None:
                rows = conn.execute(
                    """
                    SELECT id, session_id, run_id, event_type, payload_json, created_at
                    FROM reasoning_events
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, limit or 400),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, session_id, run_id, event_type, payload_json, created_at
                    FROM reasoning_events
                    WHERE session_id = ? AND run_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, run_id, limit or 400),
                ).fetchall()
            rows = list(reversed(rows))
        else:
            if run_id is None:
                rows = conn.execute(
                    """
                    SELECT id, session_id, run_id, event_type, payload_json, created_at
                    FROM reasoning_events
                    WHERE session_id = ? AND id > ?
                    ORDER BY id
                    """,
                    (session_id, after_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, session_id, run_id, event_type, payload_json, created_at
                    FROM reasoning_events
                    WHERE session_id = ? AND run_id = ? AND id > ?
                    ORDER BY id
                    """,
                    (session_id, run_id, after_id),
                ).fetchall()
    finally:
        conn.close()

    seen_ids: set[int] = set()
    for row in rows:
        seen_ids.add(row["id"])
        yield {
            "id": row["id"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
    async with _LOCK:
        _get_subscribers(session_id).append(queue)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield {"session_id": session_id, "run_id": run_id, "event_type": "heartbeat", "payload": {}}
                continue

            if event.get("id") in seen_ids:
                continue
            if event.get("id") is not None:
                seen_ids.add(event["id"])
            if run_id is not None and event.get("run_id") != run_id:
                continue
            yield event
    finally:
        async with _LOCK:
            queues = _SUBSCRIBERS.get(session_id, [])
            if queue in queues:
                queues.remove(queue)
            if not queues:
                _SUBSCRIBERS.pop(session_id, None)


def snapshot() -> dict[str, int]:
    subscribers = sum(len(queues) for queues in _SUBSCRIBERS.values())
    buffered_events = sum(queue.qsize() for queues in _SUBSCRIBERS.values() for queue in queues)
    return {
        "session_subscribers": subscribers,
        "buffered_events": buffered_events,
    }
