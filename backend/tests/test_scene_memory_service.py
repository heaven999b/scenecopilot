from __future__ import annotations


def test_scene_memory_persists_geometry_and_choice_card(isolated_runtime):
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

    observation = SceneObservation(
        summary="A control panel with a warning label and a toggle switch.",
        risk_level=RiskLevel.MEDIUM,
        structure=SceneStructure(
            layout_summary="warning label above a power switch",
            text_regions=[
                SceneElement(
                    element_id="text:warning",
                    kind="text_region",
                    label="warning label",
                    bbox_x=0.18,
                    bbox_y=0.12,
                    bbox_w=0.44,
                    bbox_h=0.22,
                )
            ],
            action_controls=[
                SceneElement(
                    element_id="control:switch",
                    kind="action_control",
                    label="power switch",
                    bbox_x=0.24,
                    bbox_y=0.58,
                    bbox_w=0.28,
                    bbox_h=0.18,
                )
            ],
        ),
        uncertainty_level="medium",
    )
    decision = ActionRecommendation(
        title="Pause before toggling the switch",
        recommendation="Inspect the warning label before touching the power control.",
        risk_level=RiskLevel.MEDIUM,
        next_steps=["Read the warning label", "Verify the safe power-down step"],
        confidence=0.78,
        approval_required=True,
        intervention_type=InterventionType.ASK_CLARIFICATION,
    )
    choice_card = ChoiceCard(
        card_type="clarification",
        headline="Need a closer look",
        rationale="The warning label is partially visible.",
        options=[
            ChoiceOption("capture_close_up", "Capture close-up", "Take a tighter frame."),
            ChoiceOption("view_manual", "View manual", "Open the related SOP."),
        ],
    )

    ids = scene_memory_service.persist_result(
        session_id="sess-memory",
        run_id="run-memory",
        prompt="What should I do here?",
        image_path="/tmp/example.jpg",
        ocr_text="Warning: isolate power before service.",
        scene_observation=observation,
        decision=decision,
        choice_card=choice_card,
    )
    capture = scene_memory_service.get_scene_capture(ids["scene_capture_id"])
    card = scene_memory_service.get_action_card(ids["action_card_id"])

    assert capture["context_json"]["scene_structure"]["text_regions"][0]["bbox_x"] == 0.18
    assert capture["context_json"]["scene_structure"]["action_controls"][0]["label"] == "power switch"
    assert card["options_json"][0]["option_id"] == "capture_close_up"
    assert card["card_type"] == "clarification"
