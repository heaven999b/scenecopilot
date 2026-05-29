from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from ..agent.tools import docs as docs_tool
from ..config import LOCAL_SPEECH_COMPUTE_TYPE, LOCAL_SPEECH_DEVICE, LOCAL_SPEECH_MODEL
from ..domain.runtime_models import (
    ActionRecommendation,
    EvidenceGap,
    FrameRef,
    InterventionType,
    OCRBlock,
    OCRResult,
    RetrievalHit,
    RiskLevel,
    SceneElement,
    SceneStructure,
    SceneObservation,
)

_LOCAL_ASR_MODEL: Any = None
_LOCAL_ASR_ERROR: str | None = None
_LOCAL_ASR_LOCK = threading.Lock()


def _bbox(x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {
        "bbox_x": x,
        "bbox_y": y,
        "bbox_w": w,
        "bbox_h": h,
    }


def _classify_risk(text: str) -> RiskLevel:
    lower = text.lower()
    if any(token in lower for token in ("danger", "warning", "caution", "hot", "voltage", "biohazard")):
        return RiskLevel.HIGH
    if any(token in lower for token in ("careful", "sharp", "heavy", "restricted", "wet floor")):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _keywords(text: str, limit: int = 6) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for raw in text.replace("/", " ").replace("-", " ").split():
        token = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(token) < 4 or token in seen:
            continue
        seen.add(token)
        words.append(token)
        if len(words) >= limit:
            break
    return words


def _workflow_state_for(lower: str) -> tuple[str, str]:
    if any(token in lower for token in ("read", "translate", "menu", "label", "sign", "manual", "instruction")):
        return (
            "inspect_text",
            "The visible scene appears text-centric, so the next useful step is to read and verify instructions before acting.",
        )
    if any(token in lower for token in ("warning", "danger", "hazard", "caution", "safe", "voltage", "hot")):
        return (
            "verify_safety",
            "The scene contains safety-oriented cues, so the workflow should pause and confirm the warning state first.",
        )
    if any(token in lower for token in ("panel", "switch", "button", "control", "lever", "machine", "device")):
        return (
            "prepare_action",
            "The scene appears to center on a device or control surface, so the workflow should verify state before interaction.",
        )
    if any(token in lower for token in ("error", "fault", "issue", "broken", "alarm", "diagnose")):
        return (
            "diagnose_issue",
            "The scene suggests troubleshooting, so the workflow should identify the fault state and compare it against a manual or SOP.",
        )
    return (
        "observe_context",
        "The scene needs broad situational interpretation before the agent commits to a more specific action path.",
    )


def _state_transitions_for(workflow_state: str) -> list[str]:
    if workflow_state == "inspect_text":
        return ["focus_text", "read_key_lines", "verify_with_manual"]
    if workflow_state == "verify_safety":
        return ["pause_action", "confirm_hazard_state", "request_review_if_unclear"]
    if workflow_state == "prepare_action":
        return ["inspect_control_state", "confirm_safe_step", "continue_cautiously"]
    if workflow_state == "diagnose_issue":
        return ["capture_error_cue", "compare_against_manual", "narrow_next_step"]
    return ["observe_scene", "find_primary_anchor", "decide_next_step"]


def _infer_scene_structure(prompt: str, ocr_text: str, frame_name: str) -> SceneStructure:
    lower = f"{prompt} {ocr_text} {frame_name}".lower()
    workflow_state, workflow_reason = _workflow_state_for(lower)
    structure = SceneStructure(
        layout_summary="Single-frame first-person scene with one dominant focal area and a small number of likely action targets.",
        workflow_state=workflow_state,
        workflow_reason=workflow_reason,
        temporal_delta_summary=(
            "Single-frame analysis only; use recent session memory and short-window aggregation to infer what changed most recently."
        ),
        attention_summary="Prioritize the strongest visible text, control surface, or warning cue before advancing the workflow.",
        state_transitions=_state_transitions_for(workflow_state),
    )
    if ocr_text.strip():
        preview = " ".join(ocr_text.split())[:80]
        text_region = SceneElement(
            element_id="text:primary",
            kind="text_region",
            label="Primary visible text",
            salience="high",
            role="evidence",
            evidence=preview,
            **_bbox(0.18, 0.18, 0.64, 0.22),
        )
        structure.text_layer.append(text_region)
        structure.text_regions.append(text_region)
        structure.primary_entry_points.append(
            SceneElement(
                element_id="entry:text",
                kind="primary_entry",
                label="Readable text region",
                salience="high",
                role="entry_point",
                evidence=preview,
                **_bbox(0.18, 0.18, 0.64, 0.22),
            )
        )
        structure.attention_targets.append(
            SceneElement(
                element_id="attention:text",
                kind="attention_target",
                label="Readable text to inspect",
                salience="high",
                role="attention_target",
                evidence=preview,
                **_bbox(0.18, 0.18, 0.64, 0.22),
            )
        )
    if any(token in lower for token in ("button", "switch", "panel", "control", "lever")):
        control_surface = SceneElement(
            element_id="control:primary",
            kind="action_control",
            label="Primary control surface",
            salience="high",
            role="action_target",
            evidence="Control-related language was detected in the prompt or OCR.",
            **_bbox(0.22, 0.58, 0.56, 0.24),
        )
        structure.action_controls.append(control_surface)
        structure.object_layer.append(
            SceneElement(
                element_id="object:control_surface",
                kind="object_cluster",
                label="Device or control panel",
                salience="high",
                role="workflow_anchor",
                evidence="The frame appears to contain an actionable device surface.",
                **_bbox(0.2, 0.5, 0.6, 0.3),
            )
        )
        structure.attention_targets.append(
            SceneElement(
                element_id="attention:control",
                kind="attention_target",
                label="Primary control to verify",
                salience="high",
                role="attention_target",
                evidence="Any action should center on the control state before proceeding.",
                **_bbox(0.22, 0.58, 0.56, 0.24),
            )
        )
    if any(token in lower for token in ("warning", "danger", "hazard", "caution", "biohazard", "voltage")):
        hazard_cue = SceneElement(
            element_id="hazard:primary",
            kind="hazard_cue",
            label="Visible warning cue",
            salience="high",
            role="risk_signal",
            evidence="Risk-related cue detected in prompt, OCR, or filename.",
            **_bbox(0.14, 0.08, 0.72, 0.16),
        )
        structure.hazard_cues.append(hazard_cue)
        structure.hazard_layer.append(hazard_cue)
        structure.attention_targets.insert(
            0,
            SceneElement(
                element_id="attention:hazard",
                kind="attention_target",
                label="Warning state to confirm",
                salience="high",
                role="attention_target",
                evidence="A visible hazard cue should be verified before continuing.",
                **_bbox(0.14, 0.08, 0.72, 0.16),
            )
        )
    if any(token in lower for token in ("menu", "label", "sign", "tag", "instruction")):
        structure.salient_elements.append(
            SceneElement(
                element_id="salient:textual",
                kind="salient_element",
                label="Text-centered focal region",
                salience="high",
                role="inspection_target",
                evidence="The scene appears text-centric.",
                **_bbox(0.16, 0.16, 0.68, 0.28),
            )
        )
    if not structure.salient_elements:
        structure.salient_elements.append(
            SceneElement(
                element_id="salient:scene",
                kind="salient_element",
                label="Primary scene focal area",
                salience="medium",
                role="inspection_target",
                evidence="No more specific structural anchor was inferred.",
                **_bbox(0.2, 0.18, 0.6, 0.5),
            )
        )
    if not structure.object_layer:
        structure.object_layer.append(
            SceneElement(
                element_id="object:scene",
                kind="object_cluster",
                label="Primary visible scene object",
                salience="medium",
                role="workflow_anchor",
                evidence="No specific control cluster was inferred, so use the general focal object as the anchor.",
                **_bbox(0.2, 0.18, 0.6, 0.5),
            )
        )
    if not structure.attention_targets:
        structure.attention_targets.append(
            SceneElement(
                element_id="attention:scene",
                kind="attention_target",
                label="Primary scene focal area",
                salience="medium",
                role="attention_target",
                evidence="Use the central focal region as the next inspection target.",
                **_bbox(0.2, 0.18, 0.6, 0.5),
            )
        )
    if structure.hazard_layer:
        structure.attention_summary = "Resolve the visible warning or hazard cue before touching controls or continuing the workflow."
    elif structure.text_layer:
        structure.attention_summary = "Read the strongest visible text or label first, then verify the next action against it."
    elif structure.object_layer:
        structure.attention_summary = "Inspect the primary device or scene object, then narrow down the next safe action."
    return structure


def _infer_evidence_gaps(prompt: str, ocr_text: str, summary: str) -> tuple[str, list[EvidenceGap]]:
    lower = f"{prompt} {ocr_text} {summary}".lower()
    gaps: list[EvidenceGap] = []
    if any(token in lower for token in ("label", "text", "warning", "manual", "instruction")) and not ocr_text.strip():
        gaps.append(
            EvidenceGap(
                gap_type="missing_text",
                reason="The request depends on visible text, but OCR evidence is weak or empty.",
                suggested_follow_up="Move closer to the text or capture a sharper close-up before deciding.",
            )
        )
    if any(token in lower for token in ("safe", "should i", "next step", "can i")) and "control" in lower and "warning" not in lower:
        gaps.append(
            EvidenceGap(
                gap_type="action_confirmation",
                reason="The scene suggests a control decision, but the state of the control surface may still be ambiguous.",
                suggested_follow_up="Ask the user to center the control panel and confirm the highlighted control or warning indicator.",
            )
        )
    if gaps:
        return "medium" if len(gaps) == 1 else "high", gaps
    return "low", []


class LocalOCRProvider:
    name = "local"

    async def extract_text(self, frame: FrameRef) -> OCRResult:
        path = Path(frame.uri)
        sidecar = path.with_suffix(".txt")
        text = ""
        if sidecar.exists():
            text = sidecar.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            text = (
                "No local OCR text was provided. In production this provider should call "
                "an OCR engine and return visible text from the frame."
            )
        return OCRResult(
            text=text,
            blocks=[OCRBlock(text=line, confidence=0.92) for line in text.splitlines()[:8]],
            provider=self.name,
        )


class LocalVisionProvider:
    name = "local"

    async def analyze_scene(self, frame: FrameRef, prompt: str, ocr_text: str = "") -> SceneObservation:
        context = f"{prompt} {ocr_text} {Path(frame.uri).name}".strip()
        lower = context.lower()
        if "menu" in lower or "price" in lower:
            summary = "The frame likely contains a menu or pricing board, so reading and summarizing visible text is the main task."
        elif "manual" in lower or "instruction" in lower or "label" in lower:
            summary = "The frame appears to contain instructions or labels that should be read before acting."
        elif any(token in lower for token in ("panel", "switch", "button", "machine", "device")):
            summary = "The frame likely shows a device or control surface, so the best next step is to identify the controls and verify safe operation."
        else:
            summary = "The frame needs general inspection, combining visible text with scene understanding before recommending an action."
        structure = _infer_scene_structure(prompt, ocr_text, Path(frame.uri).name)
        uncertainty_level, evidence_gaps = _infer_evidence_gaps(prompt, ocr_text, summary)
        return SceneObservation(
            summary=summary,
            risk_level=_classify_risk(context),
            tags=_keywords(context),
            provider=self.name,
            structure=structure,
            uncertainty_level=uncertainty_level,
            evidence_gaps=evidence_gaps,
        )


class SQLiteRetrievalProvider:
    name = "sqlite"

    async def search(self, query: str, limit: int = 5) -> list[RetrievalHit]:
        result = await docs_tool.search_documents(
            query=query,
            limit=limit,
            include_external=False,
        )
        return [
            RetrievalHit(
                document_id=item["id"],
                title=item["title"],
                snippet=item.get("snippet") or item.get("summary") or "",
                score=float(item.get("score", 0)),
                source=item.get("source") or result.get("source", self.name),
            )
            for item in result.get("items", [])
        ]


class LocalDecisionProvider:
    name = "local"

    async def recommend(
        self,
        *,
        prompt: str,
        scene_summary: str,
        ocr_text: str,
        retrieved_docs: list[RetrievalHit],
        scene_structure: SceneStructure | None = None,
        memory_context: str = "",
    ) -> ActionRecommendation:
        lower = f"{prompt} {scene_summary} {ocr_text} {memory_context}".lower()
        risk_level = _classify_risk(lower)
        doc_titles = [hit.title for hit in retrieved_docs[:4]]
        prefers_evidence = "operator_preference:evidence_first" in lower
        prefers_close_up = "operator_preference:close_up_first" in lower
        evidence_control = "operator_control_mode:evidence_control" in lower
        clarification_control = "operator_control_mode:clarify_with_image" in lower
        defer_control = "operator_control_mode:defer_control" in lower
        approval_control = "operator_control_mode:approval_control" in lower
        high_followthrough = "operator_followthrough:high" in lower
        approval_resume = "resumes an already approved action plan" in lower or "approved recommendation:" in lower
        workflow_state = scene_structure.workflow_state if scene_structure is not None else "observe_context"
        attention_targets = [item.label for item in (scene_structure.attention_targets if scene_structure is not None else [])][:3]
        attention_hint = ", ".join(attention_targets)
        if any(token in lower for token in ("read", "translate", "text")):
            return ActionRecommendation(
                title="Read the visible text first",
                recommendation="Focus on extracting the visible text, then summarize the instructions or warnings before doing anything else.",
                risk_level=risk_level,
                next_steps=[
                    "Capture or stabilize a clearer close-up if the text is blurry.",
                    "Read the headings, warnings, and numbered steps in order.",
                    "If the text is procedural, compare it against the uploaded manual before acting.",
                ],
                confidence=0.76 if ocr_text else 0.58,
                priority="medium",
                intervention_type=InterventionType.ANSWER,
                supporting_doc_titles=doc_titles,
            )
        if risk_level == RiskLevel.HIGH:
            return ActionRecommendation(
                title="Pause and verify safety",
                recommendation="The frame suggests a potentially risky situation. Stop before interacting and verify the relevant safety checklist.",
                risk_level=risk_level,
                next_steps=[
                    "Do not touch the device or area until warnings are confirmed.",
                    "Check the matching SOP or manual section.",
                    "Escalate to a trained operator if the warning state is unclear.",
                ],
                confidence=0.81,
                priority="high",
                intervention_type=InterventionType.RECOMMEND_ACTION,
                supporting_doc_titles=doc_titles,
            )
        recommendation = "Use the current scene layers and any matching documents to confirm the object state before taking the next step."
        if retrieved_docs:
            recommendation += f" I found {len(retrieved_docs)} relevant document(s) to cross-check."
        if attention_hint:
            recommendation += f" Focus first on {attention_hint}."
        if workflow_state == "inspect_text":
            recommendation += " The scene currently looks text-first, so read and verify the visible instructions before acting."
        elif workflow_state == "verify_safety":
            recommendation += " The scene currently looks safety-first, so confirm the warning state before touching the device."
        elif workflow_state == "prepare_action":
            recommendation += " The scene currently looks action-preparatory, so verify the control state before continuing."
        if prefers_evidence or evidence_control:
            recommendation += " The operator has recently preferred evidence-first review, so lead with the supporting manual or SOP before touching anything."
        if prefers_close_up or clarification_control:
            recommendation += " The operator has also shown a preference for zooming into the key label or control when evidence is weak."
        if defer_control:
            recommendation += " Keep the recommendation concise and low-pressure because the operator has recently deferred suggestions more often."
        if approval_control:
            recommendation += " If there is any ambiguity, keep the path easy to escalate for approval."
        if high_followthrough:
            recommendation += " The operator usually follows through quickly once the evidence is clear, so keep the next step direct."
        if approval_resume:
            recommendation += " This run resumes an already approved action, so the guidance should continue that plan unless the scene now contradicts it."
        return ActionRecommendation(
            title="Inspect, confirm, then act" if not approval_resume else "Continue the approved action path",
            recommendation=recommendation,
            risk_level=risk_level,
            next_steps=[
                "Capture a closer frame of the key control or label first." if (prefers_close_up or clarification_control) else "Identify the dominant object, label, or panel in the current scene window.",
                "Confirm any matching instructions from uploaded documents." if not (prefers_evidence or evidence_control) else "Open the strongest supporting manual or SOP and verify the approved step order.",
                "Proceed with the lowest-risk next action and keep capturing context if needed." if not approval_resume else "Continue with the approved current step and pause only if the scene no longer matches the approved evidence.",
            ],
            confidence=0.73 if retrieved_docs or ocr_text else 0.58,
            priority="medium",
            intervention_type=InterventionType.RECOMMEND_ACTION,
            supporting_doc_titles=doc_titles,
        )


class NoopSpeechProvider:
    name = "noop"

    async def transcribe(self, audio_path: str) -> str:
        return f"Speech provider not configured for {audio_path}."


class LocalSpeechProvider:
    name = "local"

    def _load_local_model(self):
        global _LOCAL_ASR_MODEL, _LOCAL_ASR_ERROR
        with _LOCAL_ASR_LOCK:
            if _LOCAL_ASR_MODEL is not None:
                return _LOCAL_ASR_MODEL
            if _LOCAL_ASR_ERROR is not None:
                raise RuntimeError(_LOCAL_ASR_ERROR)
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:  # pragma: no cover - optional dependency path
                _LOCAL_ASR_ERROR = f"faster-whisper unavailable: {type(exc).__name__}: {exc}"
                raise RuntimeError(_LOCAL_ASR_ERROR) from exc
            _LOCAL_ASR_MODEL = WhisperModel(
                LOCAL_SPEECH_MODEL,
                device=LOCAL_SPEECH_DEVICE,
                compute_type=LOCAL_SPEECH_COMPUTE_TYPE,
            )
            return _LOCAL_ASR_MODEL

    def _transcribe_sync(self, audio_path: str) -> str:
        model = self._load_local_model()
        segments, _info = model.transcribe(audio_path, beam_size=1)
        return " ".join(segment.text for segment in segments).strip()

    async def transcribe(self, audio_path: str) -> str:
        path = Path(audio_path)
        sidecar = path.with_suffix(".txt")
        if sidecar.exists():
            text = sidecar.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                return text
        if not path.exists() or path.stat().st_size == 0:
            return f"Audio clip missing or empty at {audio_path}."
        try:
            transcript = await asyncio.to_thread(self._transcribe_sync, audio_path)
            if transcript:
                return transcript
        except Exception as exc:
            return (
                "Audio clip received, but local speech transcription is unavailable. "
                f"Reason: {type(exc).__name__}: {exc}"
            )
        return "Audio clip received, but no speech was confidently detected."


class LocalHashEmbeddingProvider:
    name = "local_hash"

    async def embed(self, text: str) -> list[float]:
        return docs_tool.embed_text(text)


class NoopEmbeddingProvider:
    name = "noop"

    async def embed(self, text: str) -> list[float]:
        return []
