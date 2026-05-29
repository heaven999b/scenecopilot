from __future__ import annotations

from dataclasses import asdict

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ApprovalRecord, ApprovalStatus, ArtifactType, ChoiceCard, FrameRef, GroundingReference, SceneObservation
from ..orchestration.policies import (
    ClarificationPolicyDecision,
    RiskTaxonomyDecision,
    evaluate_safety_policy,
)
from .approval_step_service import approval_step_service
from .approval_service import approval_service
from .artifact_service import artifact_service
from .audit_service import audit_service
from .decision_service import decision_service
from .embedding_service import embedding_service
from .ocr_service import ocr_service
from .retrieval_service import retrieval_service
from .speech_service import speech_service
from .vision_service import vision_service


class ScenePipelineService:
    async def run_ocr(
        self,
        *,
        session_id: str,
        run_id: str,
        frame: FrameRef,
        visible_text_hint: str | None = None,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ):
        return await ocr_service.run(
            session_id=session_id,
            run_id=run_id,
            frame=frame,
            visible_text_hint=visible_text_hint,
            providers=providers,
            user_id=user_id,
        )

    async def run_asr(
        self,
        *,
        session_id: str,
        run_id: str,
        audio_path: str,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ) -> str:
        return await speech_service.run(
            session_id=session_id,
            run_id=run_id,
            audio_path=audio_path,
            providers=providers,
            user_id=user_id,
        )

    async def run_embedding(
        self,
        *,
        session_id: str,
        run_id: str,
        text: str,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ) -> list[float]:
        return await embedding_service.run(
            session_id=session_id,
            run_id=run_id,
            text=text,
            providers=providers,
            user_id=user_id,
        )

    async def run_vision(
        self,
        *,
        session_id: str,
        run_id: str,
        frame: FrameRef,
        prompt: str,
        ocr_text: str,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ):
        return await vision_service.run(
            session_id=session_id,
            run_id=run_id,
            frame=frame,
            prompt=prompt,
            ocr_text=ocr_text,
            providers=providers,
            user_id=user_id,
        )

    async def run_retrieval(
        self,
        *,
        session_id: str,
        run_id: str,
        query: str,
        providers: list[object],
        limit: int = 5,
        user_id: int = DEMO_USER_ID,
    ):
        return await retrieval_service.run(
            session_id=session_id,
            run_id=run_id,
            query=query,
            providers=providers,
            limit=limit,
            user_id=user_id,
        )

    async def run_decision(
        self,
        *,
        session_id: str,
        run_id: str,
        prompt: str,
        scene_summary: str,
        ocr_text: str,
        scene_structure,
        memory_context: str,
        retrieved_docs: list,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ):
        return await decision_service.run(
            session_id=session_id,
            run_id=run_id,
            prompt=prompt,
            scene_summary=scene_summary,
            ocr_text=ocr_text,
            scene_structure=scene_structure,
            memory_context=memory_context,
            retrieved_docs=retrieved_docs,
            providers=providers,
            user_id=user_id,
        )

    def evaluate_approval(
        self,
        *,
        session_id: str,
        run_id: str,
        recommendation,
        scene_observation: SceneObservation,
        clarification: ClarificationPolicyDecision,
        risk_taxonomy: RiskTaxonomyDecision,
        choice_card: ChoiceCard | None,
        grounding_refs: list[GroundingReference],
        ocr_text: str,
        retrieved_docs: list,
        retrieved_document_count: int,
        user_id: int = DEMO_USER_ID,
    ) -> ApprovalRecord:
        policy = evaluate_safety_policy(
            recommendation,
            risk_taxonomy=risk_taxonomy,
            clarification=clarification,
            retrieved_document_count=retrieved_document_count,
        )
        approved_steps = [
            {
                "step_id": f"approved-step-{index + 1}",
                "title": step,
                "ordinal": index + 1,
                "status": "pending",
                "approved": True,
            }
            for index, step in enumerate(recommendation.next_steps)
        ]
        packet = {
            "scene_summary": scene_observation.summary,
            "scene_structure": asdict(scene_observation.structure),
            "ocr_preview": ocr_text[:200],
            "risk_bucket": risk_taxonomy.risk_bucket,
            "risk_reason": risk_taxonomy.reason,
            "clarification_required": clarification.required,
            "clarification_question": clarification.question,
            "recommended_action": recommendation.recommendation,
            "next_steps": recommendation.next_steps,
            "approved_action_plan": approval_step_service.normalize({
                "approved_title": recommendation.title,
                "approved_recommendation": recommendation.recommendation,
                "approved_next_steps": recommendation.next_steps,
                "title": recommendation.title,
                "recommendation": recommendation.recommendation,
                "next_steps": recommendation.next_steps,
                "approved_steps": approved_steps,
                "step_cursor": 0,
                "current_step": recommendation.next_steps[0] if recommendation.next_steps else recommendation.recommendation,
                "pending_steps": recommendation.next_steps,
                "completed_steps": [],
                "step_state": "ready",
                "resume_guard": {
                    "requires_scene_match": True,
                    "recheck_on_new_hazard": True,
                    "block_on_high_uncertainty": True,
                    "allow_clarification_only_on_contradiction": True,
                },
                "supporting_doc_titles": recommendation.supporting_doc_titles,
                "grounding_refs": [asdict(item) for item in grounding_refs],
            }),
            "supporting_docs": [
                {
                    "title": getattr(hit, "title", ""),
                    "snippet": getattr(hit, "snippet", ""),
                    "score": getattr(hit, "score", 0.0),
                }
                for hit in retrieved_docs[:4]
            ],
            "grounding_refs": [asdict(item) for item in grounding_refs],
            "uncertainty_level": recommendation.uncertainty_level,
            "choice_card": {
                "card_type": choice_card.card_type,
                "headline": choice_card.headline,
                "rationale": choice_card.rationale,
                "options": [asdict(option) for option in choice_card.options],
            } if choice_card is not None else None,
        }
        approval = ApprovalRecord(
            status=ApprovalStatus.REQUIRED if policy.approval_required else ApprovalStatus.NOT_REQUIRED,
            risk_level=risk_taxonomy.risk_level,
            policy_name=f"explicit_safety_policy_v2:{policy.policy_code}",
            reason=policy.reason,
            recommended_action=recommendation.title,
            packet=packet,
        )
        approval_service.create_record(
            session_id=session_id,
            run_id=run_id,
            approval=approval,
            user_id=user_id,
        )
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.APPROVAL,
            stage="approval",
            provider="explicit_safety_policy_v2",
            content={
                "status": approval.status.value,
                "risk_level": approval.risk_level.value,
                "reason": approval.reason,
                "recommended_action": approval.recommended_action,
                "risk_bucket": risk_taxonomy.risk_bucket,
                "packet": approval.packet,
            },
            user_id=user_id,
        )
        audit_service.record(
            session_id=session_id,
            run_id=run_id,
            event_type="approval_policy_evaluated",
            detail={
                "status": approval.status.value,
                "risk_level": approval.risk_level.value,
                "reason": approval.reason,
                "blocked": policy.blocked,
                "policy_code": policy.policy_code,
                "risk_bucket": policy.risk_bucket,
            },
            user_id=user_id,
        )
        recommendation.approval_required = policy.approval_required
        recommendation.blocked = policy.blocked
        if policy.blocked:
            recommendation.priority = "high"
            recommendation.recommendation += " Human approval is required before proceeding."
        return approval


scene_pipeline_service = ScenePipelineService()
