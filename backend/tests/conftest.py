from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def isolated_runtime(tmp_path, monkeypatch):
    from app import config, db

    runtime_db = tmp_path / "scenecopilot-test.db"
    uploads_dir = tmp_path / "uploads"
    audio_chunk_dir = uploads_dir / "audio_chunks"
    frame_dir = tmp_path / "frames"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    audio_chunk_dir.mkdir(parents=True, exist_ok=True)
    frame_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DB_PATH", runtime_db, raising=False)
    monkeypatch.setattr(db, "DB_PATH", runtime_db, raising=False)
    monkeypatch.setattr(config, "UPLOADS_DIR", uploads_dir, raising=False)
    monkeypatch.setattr(config, "AUDIO_CHUNK_DIR", audio_chunk_dir, raising=False)
    monkeypatch.setattr(config, "FRAME_STASH_DIR", frame_dir, raising=False)

    db.init_db()
    from app.seed import seed

    seed()
    return {
        "db": runtime_db,
        "uploads_dir": uploads_dir,
    }
