from __future__ import annotations

from dataclasses import asdict

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType, FrameRef, OCRBlock, OCRResult
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service


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

        last_error = "No OCR provider configured."
        for provider in providers:
            try:
                result = await provider.extract_text(frame)
                artifact_service.record_artifact(
                    session_id=session_id,
                    run_id=run_id,
                    artifact_type=ArtifactType.OCR,
                    stage="ocr",
                    provider=getattr(provider, "name", provider.__class__.__name__),
                    content={
                        "text": result.text,
                        "blocks": [asdict(block) for block in result.blocks],
                    },
                    user_id=user_id,
                )
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="ocr_provider_success",
                    detail={"provider": getattr(provider, "name", "unknown")},
                    user_id=user_id,
                )
                return result
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="ocr_provider_failure",
                    detail={"provider": getattr(provider, "name", "unknown"), "error": last_error},
                    user_id=user_id,
                )
        fallback = OCRResult(text=last_error, blocks=[], provider="fallback")
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.OCR,
            stage="ocr",
            provider="fallback",
            content={"text": fallback.text, "blocks": []},
            user_id=user_id,
        )
        return fallback


ocr_service = OCRService()
