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
    ) -> ChoiceCard | None:
        if not intervention.show_choice_card:
            return None
        if intervention.intervention_type == InterventionType.ASK_CLARIFICATION:
            return ChoiceCard(
                card_type="clarification",
                headline=recommendation.title or "Need a clearer view",
                rationale=clarification.reason,
                evidence_hint=clarification.question,
                options=[
                    ChoiceOption("capture_close_up", "Capture close-up", "Take a closer or sharper frame of the key label or control."),
                    ChoiceOption("view_manual", "View manual", "Open the most relevant manual or SOP before deciding."),
                    ChoiceOption("not_now", "Not now", "Dismiss this prompt and continue observing."),
                ],
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
        options = [
            ChoiceOption("show_recommendation", "Show recommendation", "Reveal the full next-step guidance."),
            ChoiceOption("view_evidence", "View evidence", "Inspect the document grounding and scene cues."),
        ]
        if retrieved_docs:
            options.append(
                ChoiceOption(
                    "open_manual",
                    "Open manual",
                    f"Review {retrieved_docs[0].title} before acting.",
                )
            )
        options.append(
            ChoiceOption("defer", "Later", "Keep observing and revisit this suggestion later.")
        )
        return ChoiceCard(
            card_type="offer",
            headline=recommendation.title or "Available assistance",
            rationale=intervention.reason,
            evidence_hint="You can accept guidance, inspect the evidence first, or defer it.",
            options=options,
        )


choice_manager_service = ChoiceManagerService()
