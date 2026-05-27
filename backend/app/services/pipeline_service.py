from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ApprovalRecord, ApprovalStatus, ArtifactType, FrameRef
from ..orchestration.policies import evaluate_safety_policy
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
        retrieved_document_count: int,
        user_id: int = DEMO_USER_ID,
    ) -> ApprovalRecord:
        policy = evaluate_safety_policy(
            recommendation,
            retrieved_document_count=retrieved_document_count,
        )
        approval = ApprovalRecord(
            status=ApprovalStatus.REQUIRED if policy.approval_required else ApprovalStatus.NOT_REQUIRED,
            risk_level=recommendation.risk_level,
            policy_name="explicit_safety_policy_v1",
            reason=policy.reason,
            recommended_action=recommendation.title,
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
            provider="explicit_safety_policy_v1",
            content={
                "status": approval.status.value,
                "risk_level": approval.risk_level.value,
                "reason": approval.reason,
                "recommended_action": approval.recommended_action,
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
