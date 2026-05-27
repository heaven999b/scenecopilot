from __future__ import annotations

from ..domain.runtime_models import ExecutionPlan, Modality, PlanStep, PlanStepType
from .policies import choose_ocr_policy, choose_retrieval_policy


def build_default_plan(
    *,
    user_message: str,
    has_image: bool,
    has_audio: bool = False,
) -> ExecutionPlan:
    lower = user_message.lower()
    modalities: list[Modality] = [Modality.TEXT]
    steps: list[PlanStep] = []
    ocr_policy = choose_ocr_policy(user_message, has_image)
    retrieval_policy = choose_retrieval_policy(user_message, has_image)

    if has_image:
        modalities.append(Modality.IMAGE)
        steps.append(PlanStep(PlanStepType.OCR, rationale=ocr_policy.reason))
        steps.append(PlanStep(PlanStepType.VISION, rationale="The frame should be interpreted for objects and risk."))

    if has_audio:
        modalities.append(Modality.AUDIO)
        steps.append(PlanStep(PlanStepType.ASR, rationale="Audio input should be transcribed into text context."))

    if retrieval_policy.required:
        steps.append(PlanStep(PlanStepType.RETRIEVAL, rationale=retrieval_policy.reason))

    steps.append(PlanStep(PlanStepType.DECISION, rationale="Every run should end with an explicit recommendation."))
    steps.append(PlanStep(PlanStepType.APPROVAL, required=False, rationale="Safety policy may require human approval."))
    steps.append(PlanStep(PlanStepType.PERSIST, rationale="Artifacts and outputs should be replayable."))

    route_name = "fast_read" if ocr_policy.fast_path and not retrieval_policy.required else "guided_decision"
    if has_audio and not has_image:
        route_name = "audio_guided"
    return ExecutionPlan(route_name=route_name, modalities=modalities, steps=steps)
