from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType, RetrievalHit
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from .provider_runtime_service import provider_runtime_service


class RetrievalService:
    async def run(
        self,
        *,
        session_id: str,
        run_id: str,
        query: str,
        providers: list[object],
        limit: int = 5,
        user_id: int = DEMO_USER_ID,
    ) -> list[RetrievalHit]:
        execution = await provider_runtime_service.execute(
            stage="retrieval",
            session_id=session_id,
            run_id=run_id,
            providers=providers,
            invoke=lambda provider: provider.search(query, limit=limit),
            validate=lambda result: None if isinstance(result, list) else (_ for _ in ()).throw(ValueError("Retrieval provider returned an invalid hit list")),
            user_id=user_id,
        )
        if execution.value is not None:
            hits = execution.value
            artifact_service.record_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type=ArtifactType.RETRIEVAL,
                stage="retrieval",
                provider=execution.provider_name or "unknown",
                content={
                    "query": query,
                    "hits": [
                        {
                            "document_id": hit.document_id,
                            "title": hit.title,
                            "snippet": hit.snippet,
                            "score": hit.score,
                            "source": hit.source,
                        }
                        for hit in hits
                    ],
                    "provider_attempts": execution.attempts,
                    "fallback_used": execution.fallback_used,
                },
                user_id=user_id,
            )
            audit_service.record(
                session_id=session_id,
                run_id=run_id,
                event_type="retrieval_provider_success",
                detail={"provider": execution.provider_name or "unknown", "query": query, "hit_count": len(hits)},
                user_id=user_id,
            )
            return hits
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.RETRIEVAL,
            stage="retrieval",
            provider="fallback",
            content={
                "query": query,
                "hits": [],
                "error": execution.last_error,
                "provider_attempts": execution.attempts,
                "fallback_used": True,
            },
            user_id=user_id,
        )
        return []


retrieval_service = RetrievalService()
