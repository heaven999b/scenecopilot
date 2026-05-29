from __future__ import annotations

from dataclasses import asdict

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ActionRecommendation, ArtifactType, RetrievalHit, RiskLevel, SceneStructure
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from .provider_runtime_service import provider_runtime_service


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
        scene_structure: SceneStructure | None = None,
        memory_context: str = "",
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ) -> ActionRecommendation:
        execution = await provider_runtime_service.execute(
            stage="decision",
            session_id=session_id,
            run_id=run_id,
            providers=providers,
            invoke=lambda provider: provider.recommend(
                prompt=prompt,
                scene_summary=scene_summary,
                ocr_text=ocr_text,
                retrieved_docs=retrieved_docs,
                scene_structure=scene_structure,
                memory_context=memory_context,
            ),
            validate=lambda result: None if isinstance(result, ActionRecommendation) else (_ for _ in ()).throw(ValueError("Decision provider returned an invalid recommendation")),
            user_id=user_id,
        )
        if execution.value is not None:
            result = execution.value
            artifact_service.record_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type=ArtifactType.DECISION,
                stage="decision",
                provider=execution.provider_name or "unknown",
                content={
                    "title": result.title,
                    "recommendation": result.recommendation,
                    "risk_level": result.risk_level.value,
                    "next_steps": result.next_steps,
                    "confidence": result.confidence,
                    "priority": result.priority,
                    "intervention_type": result.intervention_type.value,
                    "uncertainty_level": result.uncertainty_level,
                    "clarification_question": result.clarification_question,
                    "supporting_doc_titles": result.supporting_doc_titles,
                    "grounding_refs": [asdict(item) for item in result.grounding_refs],
                    "choice_card": {
                        "card_type": result.choice_card.card_type,
                        "headline": result.choice_card.headline,
                        "rationale": result.choice_card.rationale,
                        "options": [asdict(option) for option in result.choice_card.options],
                    } if result.choice_card is not None else None,
                    "provider_attempts": execution.attempts,
                    "fallback_used": execution.fallback_used,
                },
                user_id=user_id,
            )
            audit_service.record(
                session_id=session_id,
                run_id=run_id,
                event_type="decision_provider_success",
                detail={"provider": execution.provider_name or "unknown"},
                user_id=user_id,
            )
            return result
        fallback = ActionRecommendation(
            title="Fallback decision",
            recommendation=execution.last_error,
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
                "intervention_type": fallback.intervention_type.value,
                "uncertainty_level": fallback.uncertainty_level,
                "clarification_question": fallback.clarification_question,
                "supporting_doc_titles": fallback.supporting_doc_titles,
                "grounding_refs": [],
                "choice_card": None,
                "provider_attempts": execution.attempts,
                "fallback_used": True,
            },
            user_id=user_id,
        )
        return fallback


decision_service = DecisionService()
