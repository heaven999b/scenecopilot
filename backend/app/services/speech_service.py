from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType
from .artifact_service import artifact_service
from .audit_service import audit_service
from .provider_runtime_service import provider_runtime_service


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
        execution = await provider_runtime_service.execute(
            stage="asr",
            session_id=session_id,
            run_id=run_id,
            providers=providers,
            invoke=lambda provider: provider.transcribe(audio_path),
            validate=lambda result: None if isinstance(result, str) else (_ for _ in ()).throw(ValueError("Speech provider returned a non-string transcript")),
            user_id=user_id,
        )
        if execution.value is not None:
            transcript = execution.value
            artifact_service.record_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type=ArtifactType.TRANSCRIPT,
                stage="asr",
                provider=execution.provider_name or "unknown",
                content={
                    "audio_path": audio_path,
                    "transcript": transcript,
                    "provider_attempts": execution.attempts,
                    "fallback_used": execution.fallback_used,
                },
                user_id=user_id,
            )
            audit_service.record(
                session_id=session_id,
                run_id=run_id,
                event_type="speech_provider_success",
                detail={"provider": execution.provider_name or "unknown"},
                user_id=user_id,
            )
            return transcript
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.TRANSCRIPT,
            stage="asr",
            provider="fallback",
            content={
                "audio_path": audio_path,
                "transcript": execution.last_error,
                "provider_attempts": execution.attempts,
                "fallback_used": True,
            },
            user_id=user_id,
        )
        return execution.last_error


speech_service = SpeechService()
