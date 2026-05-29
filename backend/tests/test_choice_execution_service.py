from __future__ import annotations


def test_choice_execution_persists_feedback_signal(isolated_runtime):
    from app.domain.runtime_models import (
        ActionRecommendation,
        ChoiceCard,
        ChoiceOption,
        InterventionType,
        RiskLevel,
        SceneObservation,
    )
    from app.orchestration.planner import build_default_plan
    from app.services.choice_execution_service import choice_execution_service
    from app.services.scene_memory_service import scene_memory_service
    from app.services.session_manager import session_manager

    plan = build_default_plan(
        user_message="What should I do next?",
        has_image=True,
        has_audio=False,
    )
    handle = session_manager.start_run(
        user_id=1,
        user_message="What should I do next?",
        session_id="choice-feedback-session",
        trigger="chat",
        image_count=1,
        input_payload={"image_paths": ["/tmp/example.jpg"]},
        plan=plan,
    )

    ids = scene_memory_service.persist_result(
        session_id="choice-feedback-session",
        run_id=handle.run_id,
        prompt="What should I do next?",
        image_path="/tmp/example.jpg",
        ocr_text="Warning: isolate power before service.",
        scene_observation=SceneObservation(
            summary="A warning label above a red switch.",
            risk_level=RiskLevel.MEDIUM,
        ),
        decision=ActionRecommendation(
            title="Need a closer look",
            recommendation="Zoom in before proceeding.",
            risk_level=RiskLevel.MEDIUM,
            next_steps=["Capture a tighter image of the warning label."],
            intervention_type=InterventionType.ASK_CLARIFICATION,
        ),
        choice_card=ChoiceCard(
            card_type="clarification",
            headline="Need a closer look",
            rationale="The warning label is not yet clear enough.",
            options=[
                ChoiceOption("capture_close_up", "Capture close-up", "Retake the frame with a tighter crop."),
                ChoiceOption("view_manual", "View manual", "Open the relevant SOP first."),
            ],
        ),
    )

    result = choice_execution_service.execute(
        card_id=ids["action_card_id"],
        option_id="capture_close_up",
        note="Trying again with a tighter frame.",
    )
    updated_card = scene_memory_service.get_action_card(ids["action_card_id"])

    assert result["status"] == "continued"
    assert result["continuation_state"] == "awaiting_input"
    assert updated_card["context_json"]["feedback_family"] == "clarification"
    assert updated_card["context_json"]["feedback_outcome"] == "continued"
    assert updated_card["context_json"]["feedback_signal"]["option_id"] == "capture_close_up"


def test_choice_execution_marks_approved_step_done(isolated_runtime):
    from app.domain.runtime_models import (
        ActionRecommendation,
        ChoiceCard,
        ChoiceOption,
        InterventionType,
        RiskLevel,
        SceneObservation,
    )
    from app.orchestration.planner import build_default_plan
    from app.services.choice_execution_service import choice_execution_service
    from app.services.scene_memory_service import scene_memory_service
    from app.services.session_manager import session_manager

    plan = build_default_plan(
        user_message="Continue the approved task.",
        has_image=True,
        has_audio=False,
    )
    handle = session_manager.start_run(
        user_id=1,
        user_message="Continue the approved task.",
        session_id="approved-step-session",
        trigger="approval_resume",
        image_count=1,
        input_payload={
            "image_paths": ["/tmp/example.jpg"],
            "approved_action_plan": {
                "approved_title": "Pause before toggling the switch",
                "approved_recommendation": "Read the warning label and verify the safe power-down step.",
                "approved_next_steps": ["Read the warning label", "Verify the safe power-down step"],
            },
        },
        plan=plan,
    )

    ids = scene_memory_service.persist_result(
        session_id="approved-step-session",
        run_id=handle.run_id,
        prompt="Continue the approved task.",
        image_path="/tmp/example.jpg",
        ocr_text="Warning: isolate power before service.",
        scene_observation=SceneObservation(
            summary="A warning label above a red switch.",
            risk_level=RiskLevel.MEDIUM,
        ),
        decision=ActionRecommendation(
            title="Read the warning label",
            recommendation="Continue the approved current step.",
            risk_level=RiskLevel.MEDIUM,
            next_steps=["Read the warning label", "Verify the safe power-down step"],
            intervention_type=InterventionType.RECOMMEND_ACTION,
        ),
        choice_card=ChoiceCard(
            card_type="approved_step",
            headline="Read the warning label",
            rationale="Continue one approved step at a time.",
            options=[ChoiceOption("mark_step_done", "Step done", "Advance to the next step.")],
        ),
    )

    result = choice_execution_service.execute(
        card_id=ids["action_card_id"],
        option_id="mark_step_done",
        note="Finished the current step.",
    )

    assert result["status"] == "step_advanced"
    assert result["continuation_state"] == "queued"
    assert result["evidence"]["approved_action_plan"]["current_step"] == "Verify the safe power-down step"
