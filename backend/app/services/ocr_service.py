from __future__ import annotations

from dataclasses import asdict

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType, FrameRef, OCRBlock, OCRResult
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from .provider_runtime_service import provider_runtime_service


class OCRService:
    async def run(
        self,
        *,
        session_id: str,
        run_id: str,
        frame: FrameRef,
        visible_text_hint: str | None = None,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ) -> OCRResult:
        hint = (visible_text_hint or "").strip()
        if hint:
            result = OCRResult(
                text=hint,
                blocks=[OCRBlock(text=line, confidence=1.0) for line in hint.splitlines()[:8]],
                provider="user_hint",
            )
            artifact_service.record_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type=ArtifactType.OCR,
                stage="ocr",
                provider=result.provider,
                content={
                    "text": result.text,
                    "blocks": [asdict(block) for block in result.blocks],
                },
                user_id=user_id,
            )
            audit_service.record(
                session_id=session_id,
                run_id=run_id,
                event_type="ocr_hint_used",
                detail={"provider": result.provider},
                user_id=user_id,
            )
            return result

        execution = await provider_runtime_service.execute(
            stage="ocr",
            session_id=session_id,
            run_id=run_id,
            providers=providers,
            invoke=lambda provider: provider.extract_text(frame),
            validate=lambda result: None if isinstance(result, OCRResult) else (_ for _ in ()).throw(ValueError("OCR provider returned an invalid result type")),
            user_id=user_id,
        )
        if execution.value is not None:
            result = execution.value
            artifact_service.record_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type=ArtifactType.OCR,
                stage="ocr",
                provider=execution.provider_name or getattr(result, "provider", "unknown"),
                content={
                    "text": result.text,
                    "blocks": [asdict(block) for block in result.blocks],
                    "provider_attempts": execution.attempts,
                    "fallback_used": execution.fallback_used,
                },
                user_id=user_id,
            )
            audit_service.record(
                session_id=session_id,
                run_id=run_id,
                event_type="ocr_provider_success",
                detail={"provider": execution.provider_name or "unknown"},
                user_id=user_id,
            )
            return result

        fallback = OCRResult(text=execution.last_error, blocks=[], provider="fallback")
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.OCR,
            stage="ocr",
            provider="fallback",
            content={
                "text": fallback.text,
                "blocks": [],
                "provider_attempts": execution.attempts,
                "fallback_used": True,
            },
            user_id=user_id,
        )
        return fallback


ocr_service = OCRService()
