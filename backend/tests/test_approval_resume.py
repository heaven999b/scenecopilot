from __future__ import annotations

import json


def test_approval_resume_payload_carries_approved_action_plan(isolated_runtime):
    from app.db import conn_ctx
    from app.orchestration.planner import build_default_plan
    from app.services.continuation_service import continuation_service
    from app.services.scene_memory_service import scene_memory_service
    from app.services.session_manager import session_manager
    from app.domain.runtime_models import (
        ActionRecommendation,
        ChoiceCard,
        ChoiceOption,
        InterventionType,
        RiskLevel,
        SceneObservation,
    )

    plan = build_default_plan(
        user_message="What should I do next?",
        has_image=True,
        has_audio=False,
    )
    handle = session_manager.start_run(
        user_id=1,
        user_message="What should I do next?",
        session_id="approval-resume-session",
        trigger="chat",
        image_count=1,
        input_payload={"image_paths": ["/tmp/example.jpg"]},
        plan=plan,
    )
    observation = SceneObservation(
        summary="A warning label above a red switch.",
        risk_level=RiskLevel.MEDIUM,
    )
    decision = ActionRecommendation(
        title="Pause before toggling the switch",
        recommendation="Read the warning label and verify the safe power-down step.",
        risk_level=RiskLevel.MEDIUM,
        next_steps=["Read the warning label", "Verify the safe power-down step"],
        intervention_type=InterventionType.REQUIRE_APPROVAL,
        supporting_doc_titles=["Forklift Safety SOP"],
    )
    scene_memory_service.persist_result(
        session_id="approval-resume-session",
        run_id=handle.run_id,
        prompt="What should I do next?",
        image_path="/tmp/example.jpg",
        ocr_text="Warning: isolate power before service.",
        scene_observation=observation,
        decision=decision,
        choice_card=ChoiceCard(
            card_type="approval_gate",
            headline="Approval required before proceeding",
            rationale="This is a medium-risk action.",
            options=[ChoiceOption("request_approval", "Request approval", "Escalate for approval.")],
        ),
    )
    approval_packet = {
        "recommended_action": decision.recommendation,
        "next_steps": decision.next_steps,
        "supporting_docs": [{"title": "Forklift Safety SOP"}],
        "grounding_refs": [{"anchor_label": "warning label", "action_step": "Read the warning label"}],
    }
    with conn_ctx() as conn:
        conn.execute(
            """
            INSERT INTO approval_records
              (user_id, session_id, run_id, status, risk_level, policy_name, reason, recommended_action, packet_json)
            VALUES (?, ?, ?, 'required', 'medium', 'policy', 'Needs approval', ?, ?)
            """,
            (
                1,
                "approval-resume-session",
                handle.run_id,
                decision.recommendation,
                json.dumps(approval_packet),
            ),
        )

    source_run = session_manager.get_run(handle.run_id)
    followup, payload = continuation_service.start_followup_run(
        source_run=source_run,
        continuation_reason="approval_resume",
        source_option_id="approve",
        prompt_override="Approval granted. Continue with the approved next steps.",
        requires_media=False,
        trigger="approval_resume",
        approval_packet_override=approval_packet,
        reviewer_note="Proceed with caution.",
    )

    assert followup.run_id
    assert payload["approval_resume_active"] is True
    assert payload["approved_action_plan"]["approved_title"] == "Pause before toggling the switch"
    assert payload["approved_action_plan"]["approved_next_steps"][0] == "Read the warning label"
    assert payload["approved_action_plan"]["current_step"] == "Read the warning label"
    assert payload["approved_action_plan"]["step_cursor"] == 0
    assert payload["approved_action_plan"]["pending_steps"] == decision.next_steps
    assert payload["approved_action_plan"]["approved_steps"][0]["step_id"] == "approved-step-1"
    assert payload["approved_action_plan"]["resume_guard"]["requires_scene_match"] is True
    assert payload["approved_action_plan"]["reviewer_note"] == "Proceed with caution."
