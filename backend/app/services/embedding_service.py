from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType
from .artifact_service import artifact_service
from .audit_service import audit_service


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
        last_error = "No embedding provider configured."
        for provider in providers:
            try:
                vector = await provider.embed(text)
                artifact_service.record_artifact(
                    session_id=session_id,
                    run_id=run_id,
                    artifact_type=ArtifactType.EMBEDDING,
                    stage="embedding",
                    provider=getattr(provider, "name", provider.__class__.__name__),
                    content={
                        "text_preview": text[:160],
                        "dimensions": len(vector),
                        "sample": vector[:8],
                    },
                    user_id=user_id,
                )
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="embedding_provider_success",
                    detail={
                        "provider": getattr(provider, "name", "unknown"),
                        "dimensions": len(vector),
                    },
                    user_id=user_id,
                )
                return vector
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="embedding_provider_failure",
                    detail={"provider": getattr(provider, "name", "unknown"), "error": last_error},
                    user_id=user_id,
                )
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.EMBEDDING,
            stage="embedding",
            provider="fallback",
            content={"text_preview": text[:160], "dimensions": 0, "sample": [], "error": last_error},
            user_id=user_id,
        )
        return []


embedding_service = EmbeddingService()
