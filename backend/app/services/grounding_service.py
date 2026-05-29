from __future__ import annotations

from ..domain.runtime_models import (
    ActionRecommendation,
    GroundingReference,
    RetrievalHit,
    SceneElement,
    SceneObservation,
)


def _select_anchors(scene_observation: SceneObservation) -> list[SceneElement]:
    structure = scene_observation.structure
    for collection in (
        structure.hazard_layer,
        structure.attention_targets,
        structure.object_layer,
        structure.text_layer,
        structure.hazard_cues,
        structure.action_controls,
        structure.text_regions,
        structure.primary_entry_points,
        structure.salient_elements,
    ):
        if collection:
            return collection[:3]
    return []


class GroundingService:
    def build_grounding_refs(
        self,
        *,
        scene_observation: SceneObservation,
        retrieved_docs: list[RetrievalHit],
        recommendation: ActionRecommendation,
        ocr_text: str,
    ) -> list[GroundingReference]:
        anchors = _select_anchors(scene_observation)
        steps = recommendation.next_steps[:3] or [recommendation.recommendation]
        refs: list[GroundingReference] = []

        if not anchors:
            anchors = [
                SceneElement(
                    element_id="scene:summary",
                    kind="scene_summary",
                    label="Primary scene context",
                    salience="medium",
                    role="context",
                    evidence=scene_observation.summary,
                )
            ]

        for index, step in enumerate(steps):
            anchor = anchors[min(index, len(anchors) - 1)]
            doc = retrieved_docs[index] if index < len(retrieved_docs) else (retrieved_docs[0] if retrieved_docs else None)
            support_snippet = (doc.snippet if doc is not None else ocr_text[:180] or scene_observation.summary[:180]).strip()
            rationale = (
                f"The step is grounded in '{anchor.label}' observed in the scene"
                + (
                    f" during workflow state '{scene_observation.structure.workflow_state}'"
                    if scene_observation.structure.workflow_state
                    else ""
                )
                + (f" and supported by {doc.title}." if doc is not None else ".")
            )
            refs.append(
                GroundingReference(
                    anchor_type=anchor.kind,
                    anchor_label=anchor.label,
                    action_step=step,
                    rationale=rationale,
                    doc_title=doc.title if doc is not None else None,
                    support_snippet=support_snippet or None,
                    confidence=recommendation.confidence,
                )
            )
        return refs

    def summarize_grounding(self, refs: list[GroundingReference]) -> str | None:
        if not refs:
            return None
        top = refs[0]
        if top.doc_title:
            return f"{top.anchor_label} is linked to {top.doc_title} for the next step."
        return f"{top.anchor_label} is the main evidence anchor for the next step."


grounding_service = GroundingService()
