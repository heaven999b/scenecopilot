from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from ..config import AUDIO_CHUNK_DIR, AUDIO_CHUNK_RETENTION_SEC, ORPHAN_UPLOAD_RETENTION_SEC, UPLOADS_DIR
from ..db import get_conn


class MediaLifecycleService:
    def _referenced_paths(self) -> set[str]:
        conn = get_conn()
        referenced: set[str] = set()
        try:
            for query, column in (
                ("SELECT source_path FROM documents WHERE source_path IS NOT NULL AND source_path != ''", "source_path"),
                ("SELECT image_path FROM scene_captures WHERE image_path IS NOT NULL AND image_path != ''", "image_path"),
                ("SELECT audio_path FROM audio_windows WHERE audio_path IS NOT NULL AND audio_path != ''", "audio_path"),
                ("SELECT input_json FROM runs WHERE input_json IS NOT NULL AND input_json != ''", "input_json"),
            ):
                rows = conn.execute(query).fetchall()
                for row in rows:
                    value = row[column]
                    if not value:
                        continue
                    if column != "input_json":
                        referenced.add(str(Path(value).resolve()))
                        continue
                    try:
                        payload = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                    for key in ("image_path", "audio_path"):
                        path = str(payload.get(key) or "").strip()
                        if path:
                            referenced.add(str(Path(path).resolve()))
                    for key in ("image_paths", "audio_paths"):
                        for item in payload.get(key) or []:
                            path = str(item or "").strip()
                            if path:
                                referenced.add(str(Path(path).resolve()))
        finally:
            conn.close()
        return referenced

    def cleanup_orphan_uploads(self) -> int:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        referenced = self._referenced_paths()
        now = time.time()
        removed = 0
        for path in UPLOADS_DIR.iterdir():
            if path == AUDIO_CHUNK_DIR or path.is_dir():
                continue
            if str(path.resolve()) in referenced:
                continue
            if (now - path.stat().st_mtime) < ORPHAN_UPLOAD_RETENTION_SEC:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def cleanup_audio_chunks(self) -> int:
        AUDIO_CHUNK_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        removed = 0
        for path in AUDIO_CHUNK_DIR.iterdir():
            if not path.is_dir():
                continue
            if (now - path.stat().st_mtime) < AUDIO_CHUNK_RETENTION_SEC:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        return removed

    def cleanup(self) -> dict[str, int]:
        return {
            "removed_orphan_uploads": self.cleanup_orphan_uploads(),
            "removed_audio_chunk_dirs": self.cleanup_audio_chunks(),
        }

    def snapshot(self) -> dict[str, int]:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        AUDIO_CHUNK_DIR.mkdir(parents=True, exist_ok=True)
        root_files = [path for path in UPLOADS_DIR.iterdir() if path.is_file()]
        audio_chunk_dirs = [path for path in AUDIO_CHUNK_DIR.iterdir() if path.is_dir()]
        total_upload_bytes = sum(path.stat().st_size for path in root_files if path.exists())
        total_chunk_bytes = 0
        for chunk_dir in audio_chunk_dirs:
            for child in chunk_dir.glob("**/*"):
                if child.is_file() and child.exists():
                    total_chunk_bytes += child.stat().st_size
        return {
            "upload_root_files": len(root_files),
            "upload_root_bytes": total_upload_bytes,
            "audio_chunk_dirs": len(audio_chunk_dirs),
            "audio_chunk_bytes": total_chunk_bytes,
        }


media_lifecycle_service = MediaLifecycleService()
