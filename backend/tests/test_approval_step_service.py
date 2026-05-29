from __future__ import annotations


def test_approval_step_service_advances_current_step():
    from app.services.approval_step_service import approval_step_service

    plan = approval_step_service.normalize({
        "approved_title": "Pause before toggling the switch",
        "approved_recommendation": "Read the warning label and verify the safe power-down step.",
        "approved_next_steps": ["Read the warning label", "Verify the safe power-down step"],
    })

    advanced = approval_step_service.advance(plan, note="Step completed in the field.")

    assert advanced["completed_steps"] == ["Read the warning label"]
    assert advanced["current_step"] == "Verify the safe power-down step"
    assert advanced["step_cursor"] == 1
    assert advanced["approved_steps"][0]["status"] == "completed"
