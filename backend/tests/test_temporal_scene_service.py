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
    from app.services.session_manager import session_manager
    from app.services.temporal_scene_service import temporal_scene_service
    from app.orchestration.planner import build_default_plan

    plan = build_default_plan(
        user_message="What should I do next?",
        has_image=True,
        has_audio=False,
    )
    handle = session_manager.start_run(
        user_id=1,
        user_message="What should I do next?",
        session_id="temporal-session",
        trigger="chat",
        image_count=1,
        input_payload={"image_paths": ["/tmp/prev.jpg"]},
        plan=plan,
    )

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
        run_id=handle.run_id,
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
