from __future__ import annotations

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType, FrameRef, RiskLevel, SceneObservation
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service


class VisionService:
    async def run(
        self,
        *,
        session_id: str,
        run_id: str,
        frame: FrameRef,
        prompt: str,
        ocr_text: str,
        providers: list[object],
        user_id: int = DEMO_USER_ID,
    ) -> SceneObservation:
        last_error = "No vision provider configured."
        for provider in providers:
            try:
                result = await provider.analyze_scene(frame, prompt, ocr_text)
                artifact_service.record_artifact(
                    session_id=session_id,
                    run_id=run_id,
                    artifact_type=ArtifactType.SCENE,
                    stage="vision",
                    provider=getattr(provider, "name", provider.__class__.__name__),
                    content={
                        "summary": result.summary,
                        "risk_level": result.risk_level.value,
                        "tags": result.tags,
                    },
                    user_id=user_id,
                )
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="vision_provider_success",
                    detail={"provider": getattr(provider, "name", "unknown")},
                    user_id=user_id,
                )
                return result
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                audit_service.record(
                    session_id=session_id,
                    run_id=run_id,
                    event_type="vision_provider_failure",
                    detail={"provider": getattr(provider, "name", "unknown"), "error": last_error},
                    user_id=user_id,
                )
        fallback = SceneObservation(
            summary=last_error,
            risk_level=RiskLevel.MEDIUM,
            tags=[],
            provider="fallback",
        )
        artifact_service.record_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.SCENE,
            stage="vision",
            provider="fallback",
            content={"summary": fallback.summary, "risk_level": fallback.risk_level.value, "tags": []},
            user_id=user_id,
        )
        return fallback


vision_service = VisionService()
