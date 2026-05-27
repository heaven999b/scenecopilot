from __future__ import annotations

import json
from pathlib import Path

from .config import DEMO_USER_ID, SEED_DIR
from .db import conn_ctx, init_db
from .agent.tools import docs as docs_tool

SEED_DOCS: list[tuple[str, str, list[str]]] = [
    ("Forklift Safety SOP", "forklift_safety_sop.txt", ["safety", "warehouse", "machinery"]),
    ("Wearable Quickstart", "wearable_quickstart.txt", ["wearable", "camera", "ocr"]),
    ("Menu Reading Guide", "menu_reading_guide.txt", ["accessibility", "ocr", "reading"]),
]


def _read_seed_text(filename: str) -> str:
    return (SEED_DIR / filename).read_text(encoding="utf-8")


def seed() -> None:
    init_db()
    with conn_ctx() as conn:
        conn.execute(
            """
            INSERT INTO users (id, name, role, email)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              role = excluded.role,
              email = excluded.email
            """,
            (DEMO_USER_ID, "SceneCopilot Demo User", "operator", "demo@scenecopilot.local"),
        )

        for title, filename, tags in SEED_DOCS:
            existing = conn.execute(
                "SELECT id FROM documents WHERE user_id = ? AND title = ?",
                (DEMO_USER_ID, title),
            ).fetchone()
            if existing:
                continue
            text = _read_seed_text(filename)
            summary = text.splitlines()[0].strip()
            conn.execute(
                """
                INSERT INTO documents (user_id, title, filename, source_path, text_content, summary, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEMO_USER_ID,
                    title,
                    filename,
                    str((SEED_DIR / filename).resolve()),
                    text,
                    summary,
                    json.dumps(tags),
                ),
            )

    docs_tool.rebuild_document_indexes_sync(user_id=DEMO_USER_ID)


if __name__ == "__main__":
    seed()
    print(f"Seeded SceneCopilot data from {Path(SEED_DIR).resolve()}")
