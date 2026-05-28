"""SQLite schema and helpers."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL,
  email TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  last_message_preview TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES sessions(id),
  trigger TEXT NOT NULL DEFAULT 'chat',
  status TEXT NOT NULL,
  route_name TEXT,
  user_message TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}',
  plan_json TEXT NOT NULL DEFAULT '{}',
  image_count INTEGER NOT NULL DEFAULT 0,
  queue_position INTEGER,
  current_stage TEXT,
  output_text TEXT,
  latency_ms REAL,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  filename TEXT,
  source_path TEXT,
  text_content TEXT NOT NULL DEFAULT '',
  summary TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS document_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  token_count INTEGER NOT NULL DEFAULT 0,
  embedding_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scene_captures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  session_id TEXT REFERENCES sessions(id),
  run_id TEXT REFERENCES runs(id),
  image_path TEXT,
  prompt TEXT,
  ocr_text TEXT,
  scene_summary TEXT,
  risk_level TEXT NOT NULL DEFAULT 'low',
  decisions_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS action_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  scene_capture_id INTEGER REFERENCES scene_captures(id),
  run_id TEXT REFERENCES runs(id),
  title TEXT NOT NULL,
  detail TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  session_id TEXT REFERENCES sessions(id),
  run_id TEXT REFERENCES runs(id),
  role TEXT NOT NULL,
  content TEXT,
  tool_calls_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reasoning_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES sessions(id),
  run_id TEXT REFERENCES runs(id),
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audio_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES sessions(id),
  run_id TEXT REFERENCES runs(id),
  upload_id TEXT NOT NULL,
  prompt TEXT,
  audio_path TEXT NOT NULL,
  audio_format TEXT NOT NULL DEFAULT 'binary',
  capture_profile TEXT NOT NULL DEFAULT 'balanced',
  started_at_ms INTEGER,
  ended_at_ms INTEGER,
  duration_ms INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES sessions(id),
  run_id TEXT NOT NULL REFERENCES runs(id),
  artifact_type TEXT NOT NULL,
  stage TEXT NOT NULL,
  provider TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approval_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES sessions(id),
  run_id TEXT NOT NULL REFERENCES runs(id),
  status TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  policy_name TEXT NOT NULL,
  reason TEXT NOT NULL,
  recommended_action TEXT NOT NULL,
  reviewer_note TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  session_id TEXT NOT NULL REFERENCES sessions(id),
  run_id TEXT NOT NULL REFERENCES runs(id),
  event_type TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_updated
  ON sessions(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_session_created
  ON runs(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_user_status
  ON runs(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_user_created
  ON documents(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_chunks_doc
  ON document_chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_document_chunks_user
  ON document_chunks(user_id, document_id);
CREATE INDEX IF NOT EXISTS idx_scene_captures_session
  ON scene_captures(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_cards_status
  ON action_cards(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
  ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_reasoning_events_session
  ON reasoning_events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_audio_windows_session_time
  ON audio_windows(session_id, ended_at_ms DESC, started_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_audio_windows_run
  ON audio_windows(run_id, id);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run
  ON run_artifacts(run_id, id);
CREATE INDEX IF NOT EXISTS idx_approval_records_run
  ON approval_records(run_id, id);
CREATE INDEX IF NOT EXISTS idx_run_audit_logs_run
  ON run_audit_logs(run_id, id);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
  title,
  text_content,
  summary,
  tags,
  content='documents',
  content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
  INSERT INTO documents_fts(rowid, title, text_content, summary, tags)
  VALUES (new.id, new.title, new.text_content, new.summary, new.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
  INSERT INTO documents_fts(documents_fts, rowid, title, text_content, summary, tags)
  VALUES ('delete', old.id, old.title, old.text_content, old.summary, old.tags_json);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
  INSERT INTO documents_fts(documents_fts, rowid, title, text_content, summary, tags)
  VALUES ('delete', old.id, old.title, old.text_content, old.summary, old.tags_json);
  INSERT INTO documents_fts(rowid, title, text_content, summary, tags)
  VALUES (new.id, new.title, new.text_content, new.summary, new.tags_json);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
  title,
  chunk_text,
  content='document_chunks',
  content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS document_chunks_ai AFTER INSERT ON document_chunks BEGIN
  INSERT INTO document_chunks_fts(rowid, title, chunk_text)
  VALUES (new.id, new.title, new.chunk_text);
END;

CREATE TRIGGER IF NOT EXISTS document_chunks_ad AFTER DELETE ON document_chunks BEGIN
  INSERT INTO document_chunks_fts(document_chunks_fts, rowid, title, chunk_text)
  VALUES ('delete', old.id, old.title, old.chunk_text);
END;

CREATE TRIGGER IF NOT EXISTS document_chunks_au AFTER UPDATE ON document_chunks BEGIN
  INSERT INTO document_chunks_fts(document_chunks_fts, rowid, title, chunk_text)
  VALUES ('delete', old.id, old.title, old.chunk_text);
  INSERT INTO document_chunks_fts(rowid, title, chunk_text)
  VALUES (new.id, new.title, new.chunk_text);
END;
"""

MIGRATIONS = (
    "ALTER TABLE scene_captures ADD COLUMN run_id TEXT REFERENCES runs(id)",
    "ALTER TABLE action_cards ADD COLUMN run_id TEXT REFERENCES runs(id)",
    "ALTER TABLE chat_messages ADD COLUMN run_id TEXT REFERENCES runs(id)",
    "ALTER TABLE reasoning_events ADD COLUMN run_id TEXT REFERENCES runs(id)",
    "ALTER TABLE approval_records ADD COLUMN reviewer_note TEXT",
    "ALTER TABLE audio_windows ADD COLUMN capture_profile TEXT NOT NULL DEFAULT 'balanced'",
)

POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_run ON chat_messages(run_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_reasoning_events_run ON reasoning_events(run_id, id)",
)


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -20000")
    return conn


@contextmanager
def conn_ctx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with conn_ctx() as conn:
        conn.executescript(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        for stmt in POST_MIGRATION_INDEXES:
            conn.execute(stmt)
        try:
            conn.execute("INSERT INTO documents_fts(documents_fts) VALUES ('rebuild')")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("INSERT INTO document_chunks_fts(document_chunks_fts) VALUES ('rebuild')")
        except sqlite3.OperationalError:
            pass


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in (
        "tags_json",
        "decisions_json",
        "tool_calls_json",
        "payload_json",
        "content_json",
        "detail_json",
        "input_json",
        "plan_json",
    ):
        if key in data and isinstance(data[key], str):
            try:
                data[key] = json.loads(data[key])
            except json.JSONDecodeError:
                pass
    return data
