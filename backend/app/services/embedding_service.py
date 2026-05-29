from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType
from .artifact_service import artifact_service
from .audit_service import audit_service
from .provider_runtime_service import provider_runtime_service


class EmbeddingService:
    async def run(
        self,
        *,
        session_id: str,
        run_id: str,
        text: str,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ) -> list[float]:
        execution = await provider_runtime_service.execute(
            stage="embedding",
            session_id=session_id,
            run_id=run_id,
            providers=providers,
            invoke=lambda provider: provider.embed(text),
            validate=lambda result: None if isinstance(result, list) else (_ for _ in ()).throw(ValueError("Embedding provider returned an invalid vector")),
            user_id=user_id,
        )
        if execution.value is not None:
            vector = execution.value
            artifact_service.record_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type=ArtifactType.EMBEDDING,
                stage="embedding",
                provider=execution.provider_name or "unknown",
                content={
                    "text_preview": text[:160],
                    "dimensions": len(vector),
                    "sample": vector[:8],
                    "provider_attempts": execution.attempts,
                    "fallback_used": execution.fallback_used,
                },
                user_id=user_id,
            )
            audit_service.record(
                session_id=session_id,
                run_id=run_id,
                event_type="embedding_provider_success",
                detail={
                    "provider": execution.provider_name or "unknown",
                    "dimensions": len(vector),
                },
                user_id=user_id,
            )
            return vector
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.EMBEDDING,
            stage="embedding",
            provider="fallback",
            content={
                "text_preview": text[:160],
                "dimensions": 0,
                "sample": [],
                "error": execution.last_error,
                "provider_attempts": execution.attempts,
                "fallback_used": True,
            },
            user_id=user_id,
        )
        return []


embedding_service = EmbeddingService()
