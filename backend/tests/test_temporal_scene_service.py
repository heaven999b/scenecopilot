from __future__ import annotations


def test_temporal_scene_service_tracks_workflow_transition(isolated_runtime):
    from app.domain.runtime_models import (
        ActionRecommendation,
        ChoiceCard,
        ChoiceOption,
        InterventionType,
        RiskLevel,
        SceneElement,
        SceneObservation,
        SceneStructure,
    )
    from app.services.scene_memory_service import scene_memory_service
    from app.services.temporal_scene_service import temporal_scene_service

    previous = SceneObservation(
        summary="A warning label above a switch.",
        risk_level=RiskLevel.MEDIUM,
        structure=SceneStructure(
            workflow_state="verify_safety",
            attention_targets=[
                SceneElement(
                    element_id="attention:warning",
                    kind="attention_target",
                    label="warning label",
                )
            ],
        ),
    )
    scene_memory_service.persist_result(
        session_id="temporal-session",
        run_id="temporal-run-prev",
        prompt="What should I do next?",
        image_path="/tmp/prev.jpg",
        ocr_text="Warning",
        scene_observation=previous,
        decision=ActionRecommendation(
            title="Pause",
            recommendation="Pause and inspect.",
            risk_level=RiskLevel.MEDIUM,
            intervention_type=InterventionType.ASK_CLARIFICATION,
        ),
        choice_card=ChoiceCard(
            card_type="clarification",
            headline="Pause",
            rationale="Need a clearer look.",
            options=[ChoiceOption("capture_close_up", "Capture close-up", "Retake the frame.")],
        ),
    )

    current = SceneObservation(
        summary="A control panel is now centered and ready to inspect.",
        risk_level=RiskLevel.MEDIUM,
        structure=SceneStructure(
            workflow_state="prepare_action",
            attention_targets=[
                SceneElement(
                    element_id="attention:panel",
                    kind="attention_target",
                    label="control panel",
                )
            ],
        ),
    )
    snapshot = temporal_scene_service.enrich_observation(
        session_id="temporal-session",
        scene_observation=current,
    )

    assert snapshot["previous_workflow_state"] == "verify_safety"
    assert snapshot["workflow_transition"] == "verify_safety->prepare_action"
    assert "shifted" in snapshot["temporal_delta_summary"]

