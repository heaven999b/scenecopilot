from __future__ import annotations

from dataclasses import dataclass

from ..domain.runtime_models import ActionRecommendation, RiskLevel


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


def evaluate_safety_policy(
    recommendation: ActionRecommendation,
    *,
    retrieved_document_count: int,
) -> SafetyPolicyDecision:
    if recommendation.risk_level == RiskLevel.HIGH:
        return SafetyPolicyDecision(
            blocked=True,
            approval_required=True,
            reason="High-risk recommendations must stop and wait for explicit human approval.",
        )
    if recommendation.risk_level == RiskLevel.MEDIUM and retrieved_document_count == 0:
        return SafetyPolicyDecision(
            blocked=False,
            approval_required=True,
            reason="Medium-risk guidance without grounding should require human review.",
        )
    return SafetyPolicyDecision(
        blocked=False,
        approval_required=False,
        reason="The recommendation can be presented directly without approval.",
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
