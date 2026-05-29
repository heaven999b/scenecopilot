from __future__ import annotations

from ..domain.runtime_models import (
    ActionRecommendation,
    ChoiceCard,
    ChoiceOption,
    InterventionType,
    RetrievalHit,
)
from ..orchestration.policies import ClarificationPolicyDecision, InterventionPolicyDecision, RiskTaxonomyDecision


class ChoiceManagerService:
    def build_choice_card(
        self,
        *,
        recommendation: ActionRecommendation,
        intervention: InterventionPolicyDecision,
        clarification: ClarificationPolicyDecision,
        risk_taxonomy: RiskTaxonomyDecision,
        retrieved_docs: list[RetrievalHit],
        operator_control_state: dict | None = None,
        approved_action_plan: dict | None = None,
        resume_consistency: dict | None = None,
    ) -> ChoiceCard | None:
        if not intervention.show_choice_card:
            return None
        control_mode = str((operator_control_state or {}).get("control_mode") or "").strip()
        resume_conflict = bool((resume_consistency or {}).get("conflict"))
        current_step = str((approved_action_plan or {}).get("current_step") or "").strip()
        if intervention.intervention_type == InterventionType.ASK_CLARIFICATION:
            options = [
                ChoiceOption("capture_close_up", "Capture close-up", "Take a closer or sharper frame of the key label or control."),
                ChoiceOption("view_manual", "View manual", "Open the most relevant manual or SOP before deciding."),
                ChoiceOption("not_now", "Not now", "Dismiss this prompt and continue observing."),
            ]
            if control_mode == "evidence_control":
                options = [
                    ChoiceOption("view_manual", "View manual", "Open the most relevant manual or SOP before deciding."),
                    ChoiceOption("view_evidence", "View evidence", "Inspect the current evidence and supporting docs before retaking the frame."),
                    ChoiceOption("capture_close_up", "Capture close-up", "Take a closer or sharper frame of the key label or control."),
                ]
            elif control_mode == "approval_control":
                options = [
                    ChoiceOption("view_evidence", "View evidence", "Inspect the current evidence before deciding."),
                    ChoiceOption("capture_close_up", "Capture close-up", "Retake the key scene evidence."),
                    ChoiceOption("request_approval", "Request approval", "Escalate this ambiguous case for human review.", requires_confirmation=True),
                ]
            return ChoiceCard(
                card_type="clarification_resume" if resume_conflict else "clarification",
                headline=(
                    "Re-check before resuming"
                    if resume_conflict
                    else recommendation.title or "Need a clearer view"
                ),
                rationale=clarification.reason,
                evidence_hint=clarification.question,
                options=options,
            )
        if intervention.intervention_type == InterventionType.REQUIRE_APPROVAL:
            return ChoiceCard(
                card_type="approval_gate",
                headline="Approval required before proceeding",
                rationale=risk_taxonomy.reason,
                evidence_hint="Review the scene evidence, OCR output, and supporting SOP before approving.",
                options=[
                    ChoiceOption("view_evidence", "View evidence", "Inspect the supporting scene evidence and documents."),
                    ChoiceOption("request_approval", "Request approval", "Escalate this action for human review.", requires_confirmation=True),
                    ChoiceOption("cancel", "Cancel", "Do not continue with this action."),
                ],
            )
        if approved_action_plan and current_step and not resume_conflict:
            options = [
                ChoiceOption("mark_step_done", "Step done", "Mark the current approved step as completed and move to the next one."),
                ChoiceOption("view_evidence", "View evidence", "Inspect the evidence and grounding for the current approved step."),
                ChoiceOption("pause_resume", "Pause resume", "Pause the approved path without cancelling the overall run."),
            ]
            if control_mode in {"clarify_with_image", "evidence_control"}:
                options.insert(
                    1,
                    ChoiceOption("capture_close_up", "Capture close-up", "Retake the current target if you want sharper confirmation before advancing."),
                )
            return ChoiceCard(
                card_type="approved_step",
                headline=current_step,
                rationale="This run is continuing an already approved action path. Advance one approved step at a time.",
                evidence_hint="Review the current step evidence before marking it complete if anything looks different.",
                options=options,
            )
        options = [
            ChoiceOption("show_recommendation", "Show recommendation", "Reveal the full next-step guidance."),
            ChoiceOption("view_evidence", "View evidence", "Inspect the document grounding and scene cues."),
        ]
        if control_mode == "evidence_control":
            options = [
                ChoiceOption("view_evidence", "View evidence", "Inspect the document grounding and scene cues."),
                ChoiceOption("show_recommendation", "Show recommendation", "Reveal the full next-step guidance."),
            ]
        if retrieved_docs:
            options.append(
                ChoiceOption(
                    "open_manual",
                    "Open manual",
                    f"Review {retrieved_docs[0].title} before acting.",
                )
            )
        if control_mode == "approval_control":
            options.append(
                ChoiceOption("request_approval", "Request approval", "Escalate this next step for human review.", requires_confirmation=True)
            )
        options.append(
            ChoiceOption("defer", "Later", "Keep observing and revisit this suggestion later.")
        )
        return ChoiceCard(
            card_type="offer" if intervention.intervention_type != InterventionType.LIGHTWEIGHT_OFFER else "lightweight_offer",
            headline=recommendation.title or "Available assistance",
            rationale=intervention.reason,
            evidence_hint="You can accept guidance, inspect the evidence first, or defer it.",
            options=options,
        )


choice_manager_service = ChoiceManagerService()
