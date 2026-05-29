from __future__ import annotations


def test_action_card_route_advances_approved_step(isolated_runtime, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.domain.runtime_models import (
        ActionRecommendation,
        ChoiceCard,
        ChoiceOption,
        InterventionType,
        RiskLevel,
        SceneObservation,
    )
    from app.orchestration.planner import build_default_plan
    from app.services.scene_memory_service import scene_memory_service
    from app.services.session_manager import session_manager

    monkeypatch.setattr(config, "ENABLE_WATCHER", False, raising=False)

    from app.main import app
    from app.routes import actions as actions_route

    async def fake_queue_existing_run(**kwargs):
        return 3

    monkeypatch.setattr(actions_route, "_queue_existing_run", fake_queue_existing_run)

    plan = build_default_plan(
        user_message="Continue the approved task.",
        has_image=True,
        has_audio=False,
    )
    handle = session_manager.start_run(
        user_id=1,
        user_message="Continue the approved task.",
        session_id="actions-route-session",
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
        session_id="actions-route-session",
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

    with TestClient(app) as client:
        response = client.post(
            f"/api/action-cards/{ids['action_card_id']}/execute",
            json={"option_id": "mark_step_done"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "step_advanced"
    assert payload["continuation_queue_position"] == 3
    assert payload["continuation_run_id"]
