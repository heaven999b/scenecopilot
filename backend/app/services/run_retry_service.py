from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import DEMO_USER_ID
from ..orchestration.planner import build_default_plan
from .session_manager import SessionHandle, session_manager


def _existing_paths(values: list[str]) -> list[str]:
    return [path for path in values if path and Path(path).exists()]


class RunRetryService:
    def build_retry_payload(self, source_run: dict[str, Any]) -> dict[str, Any]:
        input_json = dict(source_run.get("input_json") or {})
        image_paths = _existing_paths(list(input_json.get("image_paths") or []))
        audio_paths = _existing_paths(list(input_json.get("audio_paths") or []))

        image_path = str(input_json.get("image_path") or "").strip()
        if image_path and Path(image_path).exists() and image_path not in image_paths:
            image_paths.append(image_path)

        audio_path = str(input_json.get("audio_path") or "").strip()
        if audio_path and Path(audio_path).exists() and audio_path not in audio_paths:
            audio_paths.append(audio_path)

        payload = dict(input_json)
        payload["image_paths"] = image_paths
        payload["audio_paths"] = audio_paths
        payload["retry_of_run_id"] = source_run["id"]
        payload["retry_source_status"] = source_run.get("status")
        payload["retry_source_trigger"] = source_run.get("trigger")
        payload["missing_image_count"] = max(0, int(source_run.get("image_count") or 0) - len(image_paths))
        if audio_path and not audio_paths:
            payload["missing_audio"] = True
        return payload

    def start_retry_run(
        self,
        *,
        source_run: dict[str, Any],
        user_id: int = DEMO_USER_ID,
    ) -> tuple[SessionHandle, dict[str, Any]]:
        payload = self.build_retry_payload(source_run)
        plan = build_default_plan(
            user_message=source_run["user_message"],
            has_image=bool(payload.get("image_paths")),
            has_audio=bool(payload.get("audio_paths") or payload.get("prefetched_transcript")),
        )
        handle = session_manager.start_run(
            user_id=user_id,
            user_message=source_run["user_message"],
            session_id=source_run["session_id"],
            trigger="retry",
            image_count=len(payload.get("image_paths") or []),
            input_payload=payload,
            plan=plan,
        )
        return handle, payload


run_retry_service = RunRetryService()
