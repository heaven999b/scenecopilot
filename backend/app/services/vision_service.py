from __future__ import annotations

from dataclasses import asdict

from ..config import DEMO_USER_ID
from ..domain.runtime_models import ArtifactType, FrameRef, RiskLevel, SceneObservation
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from .provider_runtime_service import provider_runtime_service


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
        execution = await provider_runtime_service.execute(
            stage="vision",
            session_id=session_id,
            run_id=run_id,
            providers=providers,
            invoke=lambda provider: provider.analyze_scene(frame, prompt, ocr_text),
            validate=lambda result: None if isinstance(result, SceneObservation) else (_ for _ in ()).throw(ValueError("Vision provider returned an invalid scene observation")),
            user_id=user_id,
        )
        if execution.value is not None:
            result = execution.value
            artifact_service.record_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type=ArtifactType.SCENE,
                stage="vision",
                provider=execution.provider_name or getattr(result, "provider", "unknown"),
                content={
                    "summary": result.summary,
                    "risk_level": result.risk_level.value,
                    "tags": result.tags,
                    "uncertainty_level": result.uncertainty_level,
                    "structure": {
                        "layout_summary": result.structure.layout_summary,
                        "primary_entry_points": [asdict(item) for item in result.structure.primary_entry_points],
                        "text_regions": [asdict(item) for item in result.structure.text_regions],
                        "action_controls": [asdict(item) for item in result.structure.action_controls],
                        "hazard_cues": [asdict(item) for item in result.structure.hazard_cues],
                        "overlays": [asdict(item) for item in result.structure.overlays],
                        "salient_elements": [asdict(item) for item in result.structure.salient_elements],
                    },
                    "evidence_gaps": [asdict(item) for item in result.evidence_gaps],
                    "provider_attempts": execution.attempts,
                    "fallback_used": execution.fallback_used,
                },
                user_id=user_id,
            )
            audit_service.record(
                session_id=session_id,
                run_id=run_id,
                event_type="vision_provider_success",
                detail={"provider": execution.provider_name or "unknown"},
                user_id=user_id,
            )
            return result

        fallback = SceneObservation(
            summary=execution.last_error,
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
            content={
                "summary": fallback.summary,
                "risk_level": fallback.risk_level.value,
                "tags": [],
                "uncertainty_level": fallback.uncertainty_level,
                "structure": {"layout_summary": "", "primary_entry_points": [], "text_regions": [], "action_controls": [], "hazard_cues": [], "overlays": [], "salient_elements": []},
                "evidence_gaps": [],
                "provider_attempts": execution.attempts,
                "fallback_used": True,
            },
            user_id=user_id,
        )
        return fallback


vision_service = VisionService()
