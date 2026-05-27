from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ActionRecommendation, ArtifactType, RetrievalHit, RiskLevel
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service


class DecisionService:
    async def run(
        self,
        *,
        session_id: str,
        run_id: str,
        prompt: str,
        scene_summary: str,
        ocr_text: str,
        retrieved_docs: list[RetrievalHit],
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ) -> ActionRecommendation:
        last_error = "No decision provider configured."
        for provider in providers:
            try:
                result = await provider.recommend(
                    prompt=prompt,
                    scene_summary=scene_summary,
                    ocr_text=ocr_text,
                    retrieved_docs=retrieved_docs,
                )
                artifact_service.record_artifact(
                    session_id=session_id,
                    run_id=run_id,
                    artifact_type=ArtifactType.DECISION,
                    stage="decision",
                    provider=getattr(provider, "name", provider.__class__.__name__),
                    content={
                        "title": result.title,
                        "recommendation": result.recommendation,
                        "risk_level": result.risk_level.value,
                        "next_steps": result.next_steps,
                        "confidence": result.confidence,
                        "priority": result.priority,
                    },
                    user_id=user_id,
                )
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="decision_provider_success",
                    detail={"provider": getattr(provider, "name", "unknown")},
                    user_id=user_id,
                )
                return result
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="decision_provider_failure",
                    detail={"provider": getattr(provider, "name", "unknown"), "error": last_error},
                    user_id=user_id,
                )
        fallback = ActionRecommendation(
            title="Fallback decision",
            recommendation=last_error,
            risk_level=RiskLevel.MEDIUM,
            next_steps=["Retry the request or inspect the artifacts for provider failures."],
            confidence=0.0,
            priority="medium",
        )
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.DECISION,
            stage="decision",
            provider="fallback",
            content={
                "title": fallback.title,
                "recommendation": fallback.recommendation,
                "risk_level": fallback.risk_level.value,
                "next_steps": fallback.next_steps,
                "confidence": fallback.confidence,
                "priority": fallback.priority,
            },
            user_id=user_id,
        )
        return fallback


decision_service = DecisionService()
