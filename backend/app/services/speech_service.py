from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType
from .artifact_service import artifact_service
from .audit_service import audit_service


class SpeechService:
    async def run(
        self,
        *,
        session_id: str,
        run_id: str,
        audio_path: str,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ) -> str:
        last_error = "No speech provider configured."
        for provider in providers:
            try:
                transcript = await provider.transcribe(audio_path)
                artifact_service.record_artifact(
                    session_id=session_id,
                    run_id=run_id,
                    artifact_type=ArtifactType.TRANSCRIPT,
                    stage="asr",
                    provider=getattr(provider, "name", provider.__class__.__name__),
                    content={
                        "audio_path": audio_path,
                        "transcript": transcript,
                    },
                    user_id=user_id,
                )
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="speech_provider_success",
                    detail={"provider": getattr(provider, "name", "unknown")},
                    user_id=user_id,
                )
                return transcript
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="speech_provider_failure",
                    detail={"provider": getattr(provider, "name", "unknown"), "error": last_error},
                    user_id=user_id,
                )
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.TRANSCRIPT,
            stage="asr",
            provider="fallback",
            content={"audio_path": audio_path, "transcript": last_error},
            user_id=user_id,
        )
        return last_error


speech_service = SpeechService()
