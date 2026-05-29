from __future__ import annotations


def test_followup_run_requires_media(isolated_runtime):
    from app.orchestration.planner import build_default_plan
    from app.services.continuation_service import continuation_service
    from app.services.session_manager import session_manager

    plan = build_default_plan(
        user_message="Read the warning label and tell me the next step.",
        has_image=True,
        has_audio=False,
    )
    handle = session_manager.start_run(
        user_id=1,
        user_message="Read the warning label and tell me the next step.",
        session_id="sess-test",
        trigger="chat",
        image_count=1,
        input_payload={"image_paths": [], "audio_paths": []},
        plan=plan,
    )
    source_run = session_manager.get_run(handle.run_id)
    followup, payload = continuation_service.start_followup_run(
        source_run=source_run,
        continuation_reason="clarification_followup",
        source_option_id="capture_close_up",
        requires_media=True,
        trigger="clarification_followup",
    )
    child_run = session_manager.get_run(followup.run_id)

    assert child_run["status"] == "awaiting_input"
    assert child_run["input_json"]["parent_run_id"] == handle.run_id
    assert child_run["input_json"]["continuation_reason"] == "clarification_followup"
    assert payload["required_followup_media"] == "image"
