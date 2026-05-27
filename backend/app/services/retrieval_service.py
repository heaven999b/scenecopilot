from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType, RetrievalHit
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service


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
        last_error = "No retrieval provider configured."
        for provider in providers:
            try:
                hits = await provider.search(query, limit=limit)
                artifact_service.record_artifact(
                    session_id=session_id,
                    run_id=run_id,
                    artifact_type=ArtifactType.RETRIEVAL,
                    stage="retrieval",
                    provider=getattr(provider, "name", provider.__class__.__name__),
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
                    },
                    user_id=user_id,
                )
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="retrieval_provider_success",
                    detail={"provider": getattr(provider, "name", "unknown"), "query": query, "hit_count": len(hits)},
                    user_id=user_id,
                )
                return hits
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="retrieval_provider_failure",
                    detail={"provider": getattr(provider, "name", "unknown"), "error": last_error},
                    user_id=user_id,
                )
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.RETRIEVAL,
            stage="retrieval",
            provider="fallback",
            content={"query": query, "hits": [], "error": last_error},
            user_id=user_id,
        )
        return []


retrieval_service = RetrievalService()
