from __future__ import annotations

from dataclasses import dataclass

from ..domain.runtime_models import (
    ActionRecommendation,
    EvidenceGap,
    InterventionType,
    RiskLevel,
    SceneObservation,
)


@dataclass(slots=True)
class RetrievalPolicyDecision:
    required: bool
    reason: str


@dataclass(slots=True)
class OcrPolicyDecision:
    fast_path: bool
    reason: str


@dataclass(slots=True)
class SafetyPolicyDecision:
    blocked: bool
    approval_required: bool
    reason: str
    policy_code: str = "allow_direct"
    risk_bucket: str = "informational"


@dataclass(slots=True)
class ClarificationPolicyDecision:
    required: bool
    reason: str
    question: str | None = None
    suggested_options: list[str] | None = None


@dataclass(slots=True)
class RiskTaxonomyDecision:
    risk_level: RiskLevel
    risk_bucket: str
    approval_mode: str
    reason: str
    irreversible: bool = False


@dataclass(slots=True)
class InterventionPolicyDecision:
    intervention_type: InterventionType
    show_choice_card: bool
    reason: str


def choose_ocr_policy(user_message: str, has_image: bool) -> OcrPolicyDecision:
    lower = user_message.lower()
    if not has_image:
        return OcrPolicyDecision(fast_path=False, reason="No image input is available.")
    if any(token in lower for token in ("read", "translate", "menu", "label", "sign", "text")):
        return OcrPolicyDecision(
            fast_path=True,
            reason="The request is explicitly text-first, so OCR should run in the lowest-latency path.",
        )
    return OcrPolicyDecision(
        fast_path=False,
        reason="The request needs broader scene context, so OCR should feed a full multimodal analysis.",
    )


def choose_retrieval_policy(user_message: str, has_image: bool, risk_hint: str | None = None) -> RetrievalPolicyDecision:
    lower = user_message.lower()
    if any(token in lower for token in ("manual", "sop", "guide", "compare", "procedure")):
        return RetrievalPolicyDecision(True, "The user explicitly asked for grounded procedural knowledge.")
    if any(token in lower for token in ("should i", "next step", "what do i do", "warning", "danger", "safe")):
        return RetrievalPolicyDecision(True, "Decision support and safety questions should be grounded in documents.")
    if has_image and risk_hint == RiskLevel.HIGH.value:
        return RetrievalPolicyDecision(True, "High-risk situations should retrieve relevant safety documents.")
    return RetrievalPolicyDecision(False, "Retrieval is optional for this request.")


def evaluate_clarification_policy(
    *,
    user_message: str,
    ocr_text: str,
    scene_observation: SceneObservation,
    retrieved_document_count: int,
    operator_control_state: dict | None = None,
) -> ClarificationPolicyDecision:
    lower = user_message.lower()
    evidence_gaps = scene_observation.evidence_gaps
    control_mode = str((operator_control_state or {}).get("control_mode") or "").strip()
    preferred_options = ["capture_close_up", "view_manual", "not_now"]
    if control_mode == "evidence_control":
        preferred_options = ["view_manual", "view_evidence", "capture_close_up"]
    elif control_mode == "approval_control":
        preferred_options = ["view_evidence", "capture_close_up", "request_approval"]
    if evidence_gaps and scene_observation.uncertainty_level in {"medium", "high"}:
        top_gap = evidence_gaps[0]
        return ClarificationPolicyDecision(
            required=True,
            reason=top_gap.reason,
            question=top_gap.suggested_follow_up,
            suggested_options=preferred_options,
        )
    if any(token in lower for token in ("label", "text", "warning", "expiry", "expiration")) and not ocr_text.strip():
        return ClarificationPolicyDecision(
            required=True,
            reason="The request depends on visible text, but the current OCR evidence is empty.",
            question="I need a clearer close-up of the label or warning text before I can answer confidently. Can you zoom in or retake the frame?",
            suggested_options=preferred_options,
        )
    if retrieved_document_count == 0 and any(token in lower for token in ("safe", "should i", "next step", "can i")):
        return ClarificationPolicyDecision(
            required=True,
            reason="The request is action-oriented, but there is not yet enough grounded evidence to recommend a safe next step.",
            question="I can help once I have a clearer view or a matching SOP. Do you want to retake the frame or open the relevant manual first?",
            suggested_options=["capture_close_up", "open_manual", "cancel"] if control_mode != "evidence_control" else ["open_manual", "view_evidence", "capture_close_up"],
        )
    return ClarificationPolicyDecision(required=False, reason="The current scene evidence is sufficient.")


def classify_risk_taxonomy(
    *,
    user_message: str,
    scene_observation: SceneObservation,
    recommendation: ActionRecommendation,
    retrieved_document_count: int,
) -> RiskTaxonomyDecision:
    lower = f"{user_message} {scene_observation.summary}".lower()
    hazard_count = max(
        len(scene_observation.structure.hazard_cues),
        len(scene_observation.structure.hazard_layer),
    )
    if recommendation.risk_level == RiskLevel.HIGH or hazard_count >= 1:
        return RiskTaxonomyDecision(
            risk_level=RiskLevel.HIGH,
            risk_bucket="safety_critical",
            approval_mode="mandatory",
            reason="The scene contains a high-risk or explicitly hazardous cue.",
            irreversible=any(token in lower for token in ("power", "switch", "energize", "chemical", "exposure", "override")),
        )
    if recommendation.risk_level == RiskLevel.MEDIUM and retrieved_document_count == 0:
        return RiskTaxonomyDecision(
            risk_level=RiskLevel.MEDIUM,
            risk_bucket="procedural_uncertain",
            approval_mode="grounding_required",
            reason="The recommendation carries medium risk but lacks supporting documentation.",
            irreversible=any(token in lower for token in ("delete", "erase", "reset", "shut down")),
        )
    if recommendation.risk_level == RiskLevel.MEDIUM:
        return RiskTaxonomyDecision(
            risk_level=RiskLevel.MEDIUM,
            risk_bucket="cautionary_guidance",
            approval_mode="review_optional",
            reason="The scene requires caution, but it has at least some supporting evidence.",
            irreversible=False,
        )
    return RiskTaxonomyDecision(
        risk_level=RiskLevel.LOW,
        risk_bucket="informational",
        approval_mode="direct",
        reason="The current recommendation is informational or low-risk.",
        irreversible=False,
    )


def evaluate_safety_policy(
    recommendation: ActionRecommendation,
    *,
    risk_taxonomy: RiskTaxonomyDecision,
    clarification: ClarificationPolicyDecision,
    retrieved_document_count: int,
) -> SafetyPolicyDecision:
    if clarification.required and risk_taxonomy.risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH}:
        return SafetyPolicyDecision(
            blocked=True,
            approval_required=True,
            reason="High-uncertainty guidance in a risky scene must stop and ask for human confirmation or a clearer view.",
            policy_code="clarify_then_approve",
            risk_bucket=risk_taxonomy.risk_bucket,
        )
    if risk_taxonomy.risk_level == RiskLevel.HIGH:
        return SafetyPolicyDecision(
            blocked=True,
            approval_required=True,
            reason="High-risk recommendations must stop and wait for explicit human approval.",
            policy_code="high_risk_block",
            risk_bucket=risk_taxonomy.risk_bucket,
        )
    if risk_taxonomy.risk_level == RiskLevel.MEDIUM and retrieved_document_count == 0:
        return SafetyPolicyDecision(
            blocked=False,
            approval_required=True,
            reason="Medium-risk guidance without grounding should require human review.",
            policy_code="medium_risk_grounding_review",
            risk_bucket=risk_taxonomy.risk_bucket,
        )
    return SafetyPolicyDecision(
        blocked=False,
        approval_required=False,
        reason="The recommendation can be presented directly without approval.",
        policy_code="allow_direct",
        risk_bucket=risk_taxonomy.risk_bucket,
    )


def requires_human_approval(recommendation: ActionRecommendation) -> bool:
    return recommendation.risk_level == RiskLevel.HIGH


def choose_latency_tier(user_message: str, has_image: bool) -> str:
    lower = user_message.lower()
    if not has_image:
        return "text_only"
    if any(token in lower for token in ("read", "translate", "what does this say")):
        return "fast"
    if any(token in lower for token in ("safe", "danger", "warning", "should i", "next step")):
        return "guided"
    return "balanced"


def choose_intervention_policy(
    *,
    recommendation: ActionRecommendation,
    clarification: ClarificationPolicyDecision,
    risk_taxonomy: RiskTaxonomyDecision,
    operator_control_state: dict | None = None,
) -> InterventionPolicyDecision:
    control_mode = str((operator_control_state or {}).get("control_mode") or "").strip()
    if clarification.required:
        return InterventionPolicyDecision(
            intervention_type=InterventionType.ASK_CLARIFICATION,
            show_choice_card=True,
            reason="The current evidence is insufficient, so the agent should clarify before recommending an action.",
        )
    if risk_taxonomy.approval_mode == "mandatory":
        return InterventionPolicyDecision(
            intervention_type=InterventionType.REQUIRE_APPROVAL,
            show_choice_card=True,
            reason="The recommendation is safety-critical and must be escalated for approval.",
        )
    if recommendation.intervention_type == InterventionType.ANSWER:
        return InterventionPolicyDecision(
            intervention_type=InterventionType.ANSWER,
            show_choice_card=False,
            reason="The request is primarily informational and can be answered directly.",
        )
    if control_mode == "defer_control" and risk_taxonomy.risk_level == RiskLevel.LOW:
        return InterventionPolicyDecision(
            intervention_type=InterventionType.LIGHTWEIGHT_OFFER,
            show_choice_card=True,
            reason="The operator has recently deferred low-risk prompts, so surface a lighter-weight offer instead of a pushy action card.",
        )
    return InterventionPolicyDecision(
        intervention_type=InterventionType.RECOMMEND_ACTION,
        show_choice_card=True,
        reason="The agent should surface a controllable next-step recommendation with explicit options.",
    )
