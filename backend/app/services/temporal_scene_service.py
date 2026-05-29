from __future__ import annotations

from typing import Any

from ..domain.runtime_models import SceneElement, SceneObservation
from .scene_memory_service import scene_memory_service


def _labels(elements: list[SceneElement]) -> list[str]:
    return [item.label.strip() for item in elements if item.label.strip()]


def _elements_by_labels(elements: list[SceneElement], labels: list[str]) -> list[SceneElement]:
    wanted = {label for label in labels if label}
    return [item for item in elements if item.label in wanted]


class TemporalSceneService:
    def enrich_observation(
        self,
        *,
        session_id: str,
        scene_observation: SceneObservation,
    ) -> dict[str, Any]:
        recent_captures = scene_memory_service.list_recent_session_captures(session_id, limit=2)
        previous = recent_captures[0] if recent_captures else None
        previous_context = previous.get("context_json") if isinstance(previous, dict) else {}
        previous_structure = (
            previous_context.get("scene_structure") if isinstance(previous_context, dict) else {}
        ) or {}

        previous_workflow = str(previous_structure.get("workflow_state") or "").strip()
        current_workflow = scene_observation.structure.workflow_state
        previous_attention = [
            str(item.get("label") or "").strip()
            for item in previous_structure.get("attention_targets", [])
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ]
        current_attention = _labels(scene_observation.structure.attention_targets)

        persistent = [label for label in current_attention if label in previous_attention]
        emerging = [label for label in current_attention if label not in previous_attention]

        if previous_workflow and current_workflow and previous_workflow != current_workflow:
            workflow_transition = f"{previous_workflow}->{current_workflow}"
            temporal_delta = (
                f"The scene workflow shifted from {previous_workflow} to {current_workflow}. "
                + (
                    f"New attention targets: {', '.join(emerging[:3])}."
                    if emerging
                    else "The focus changed even though the same visible targets persist."
                )
            )
        elif previous_workflow and current_workflow == previous_workflow:
            workflow_transition = "stable"
            temporal_delta = (
                f"The scene remains in {current_workflow}. "
                + (
                    f"Persistent attention targets: {', '.join(persistent[:3])}."
                    if persistent
                    else "The workflow is stable, but the visible focus points have shifted slightly."
                )
            )
        elif current_workflow:
            workflow_transition = "new_focus"
            temporal_delta = f"No prior scene window was available, so {current_workflow} is treated as the initial workflow state."
        else:
            workflow_transition = "unknown"
            temporal_delta = "The scene workflow could not be compared against recent history."

        scene_observation.structure.previous_workflow_state = previous_workflow
        scene_observation.structure.workflow_transition = workflow_transition
        scene_observation.structure.persistent_attention_targets = _elements_by_labels(
            scene_observation.structure.attention_targets,
            persistent,
        )
        scene_observation.structure.emerging_attention_targets = _elements_by_labels(
            scene_observation.structure.attention_targets,
            emerging,
        )
        scene_observation.structure.temporal_delta_summary = temporal_delta
        if persistent and scene_observation.structure.attention_summary:
            scene_observation.structure.attention_summary += (
                f" Persistent focus: {', '.join(persistent[:2])}."
            )
        elif emerging and scene_observation.structure.attention_summary:
            scene_observation.structure.attention_summary += (
                f" Newly important target: {', '.join(emerging[:2])}."
            )

        return {
            "previous_workflow_state": previous_workflow or None,
            "workflow_state": current_workflow,
            "workflow_transition": workflow_transition,
            "persistent_attention_labels": persistent,
            "emerging_attention_labels": emerging,
            "temporal_delta_summary": temporal_delta,
        }


temporal_scene_service = TemporalSceneService()
