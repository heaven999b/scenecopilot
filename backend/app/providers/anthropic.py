from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any

from ..config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_IMAGE_JPEG_QUALITY,
    ANTHROPIC_IMAGE_MAX_SIDE,
    ANTHROPIC_DECISION_MODEL,
    ANTHROPIC_MAX_TOKENS,
    ANTHROPIC_VISION_MODEL,
)
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

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover - optional runtime dependency in local env
    AsyncAnthropic = None

try:  # pragma: no cover - optional runtime dependency in local env
    from PIL import Image
except ImportError:  # pragma: no cover - optional runtime dependency in local env
    Image = None


def _coerce_risk(value: str | None) -> RiskLevel:
    normalized = (value or "").strip().lower()
    if normalized == RiskLevel.HIGH.value:
        return RiskLevel.HIGH
    if normalized == RiskLevel.MEDIUM.value:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _extract_text_blocks(payload: str) -> list[OCRBlock]:
    blocks = []
    for line in payload.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        blocks.append(OCRBlock(text=stripped, confidence=0.88))
        if len(blocks) >= 12:
            break
    return blocks


def _coerce_element(
    item: Any,
    *,
    element_id: str,
    kind: str,
    role: str,
    salience: str = "high",
    fallback_bbox: tuple[float, float, float, float] | None = None,
) -> SceneElement | None:
    if isinstance(item, dict):
        label = str(item.get("label") or item.get("text") or "").strip()
        if not label:
            return None
        bbox = fallback_bbox or (None, None, None, None)
        return SceneElement(
            element_id=element_id,
            kind=kind,
            label=label,
            salience=str(item.get("salience") or salience),
            role=role,
            evidence=str(item.get("evidence") or "").strip() or None,
            bbox_x=float(item["bbox_x"]) if item.get("bbox_x") is not None else bbox[0],
            bbox_y=float(item["bbox_y"]) if item.get("bbox_y") is not None else bbox[1],
            bbox_w=float(item["bbox_w"]) if item.get("bbox_w") is not None else bbox[2],
            bbox_h=float(item["bbox_h"]) if item.get("bbox_h") is not None else bbox[3],
        )
    label = str(item).strip()
    if not label:
        return None
    bbox = fallback_bbox or (None, None, None, None)
    return SceneElement(
        element_id=element_id,
        kind=kind,
        label=label,
        salience=salience,
        role=role,
        bbox_x=bbox[0],
        bbox_y=bbox[1],
        bbox_w=bbox[2],
        bbox_h=bbox[3],
    )


class _AnthropicBaseProvider:
    name = "anthropic"

    def __init__(self, model: str) -> None:
        self.model = model
        self._client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY) if AsyncAnthropic and ANTHROPIC_API_KEY else None

    def _ensure_client(self) -> AsyncAnthropic:
        if self._client is None:
            raise RuntimeError("Anthropic provider requested but ANTHROPIC_API_KEY or SDK is unavailable.")
        return self._client

    @staticmethod
    def _image_content(frame: FrameRef) -> dict[str, Any]:
        path = Path(frame.uri)
        mime_type = frame.mime_type
        payload = path.read_bytes()
        if Image is not None:
            try:
                with Image.open(path) as image:
                    image.load()
                    if max(image.size) > ANTHROPIC_IMAGE_MAX_SIDE:
                        image.thumbnail((ANTHROPIC_IMAGE_MAX_SIDE, ANTHROPIC_IMAGE_MAX_SIDE))
                    output = BytesIO()
                    save_format = "PNG" if mime_type == "image/png" else "JPEG"
                    working = image
                    if save_format == "JPEG" and image.mode not in ("RGB", "L"):
                        working = image.convert("RGB")
                    save_kwargs = {"format": save_format, "optimize": True}
                    if save_format == "JPEG":
                        save_kwargs["quality"] = ANTHROPIC_IMAGE_JPEG_QUALITY
                    working.save(output, **save_kwargs)
                    payload = output.getvalue()
                    mime_type = "image/png" if save_format == "PNG" else "image/jpeg"
            except Exception:
                payload = path.read_bytes()
        encoded = base64.standard_b64encode(payload).decode("utf-8")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": encoded,
            },
        }

    @staticmethod
    def _text_from_message(message: Any) -> str:
        parts: list[str] = []
        for block in getattr(message, "content", []):
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    async def _json_completion(self, *, prompt: str, frame: FrameRef | None = None) -> dict[str, Any]:
        client = self._ensure_client()
        content: list[dict[str, Any]] = []
        if frame is not None:
            content.append(self._image_content(frame))
        content.append({"type": "text", "text": prompt})
        message = await client.messages.create(
            model=self.model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            messages=[{"role": "user", "content": content}],
        )
        raw = self._text_from_message(message)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                return json.loads(raw[start : end + 1])
            raise RuntimeError(f"Anthropic provider returned non-JSON payload: {raw[:240]}")


class AnthropicOCRProvider(_AnthropicBaseProvider):
    def __init__(self) -> None:
        super().__init__(ANTHROPIC_VISION_MODEL)

    async def extract_text(self, frame: FrameRef) -> OCRResult:
        payload = await self._json_completion(
            frame=frame,
            prompt=(
                "Read all visible text from this image. Return strict JSON with one key: "
                '{"text":"full extracted text"}'
            ),
        )
        text = str(payload.get("text", "")).strip()
        return OCRResult(
            text=text,
            blocks=_extract_text_blocks(text),
            provider=self.name,
        )


class AnthropicVisionProvider(_AnthropicBaseProvider):
    def __init__(self) -> None:
        super().__init__(ANTHROPIC_VISION_MODEL)

    async def analyze_scene(self, frame: FrameRef, prompt: str, ocr_text: str = "") -> SceneObservation:
        payload = await self._json_completion(
            frame=frame,
            prompt=(
                "Analyze this first-person scene for a wearable AI assistant. "
                "Consider the user prompt and OCR text. "
                'Return strict JSON with keys {"summary": string, "risk_level": "low"|"medium"|"high", '
                '"tags": string[], "uncertainty_level": "low"|"medium"|"high", '
                '"layout_summary": string, '
                '"workflow_state": string, "workflow_reason": string, "temporal_delta_summary": string, "attention_summary": string, '
                '"text_layer": [{"label": string, "bbox_x": number, "bbox_y": number, "bbox_w": number, "bbox_h": number}], '
                '"object_layer": [{"label": string, "bbox_x": number, "bbox_y": number, "bbox_w": number, "bbox_h": number}], '
                '"hazard_layer": [{"label": string, "bbox_x": number, "bbox_y": number, "bbox_w": number, "bbox_h": number}], '
                '"attention_targets": [{"label": string, "bbox_x": number, "bbox_y": number, "bbox_w": number, "bbox_h": number}], '
                '"state_transitions": string[], '
                '"primary_entry_points": [{"label": string, "bbox_x": number, "bbox_y": number, "bbox_w": number, "bbox_h": number}], '
                '"text_regions": [{"label": string, "bbox_x": number, "bbox_y": number, "bbox_w": number, "bbox_h": number}], '
                '"action_controls": [{"label": string, "bbox_x": number, "bbox_y": number, "bbox_w": number, "bbox_h": number}], '
                '"hazard_cues": [{"label": string, "bbox_x": number, "bbox_y": number, "bbox_w": number, "bbox_h": number}], '
                '"evidence_gaps": [{"gap_type": string, "reason": string, "suggested_follow_up": string}]}. '
                f"User prompt: {prompt}\nOCR text: {ocr_text}"
            ),
        )
        primary_entry_points = [
            item
            for idx, raw in enumerate(payload.get("primary_entry_points", []))
            if (item := _coerce_element(raw, element_id=f"entry:{idx}", kind="primary_entry", role="entry_point", fallback_bbox=(0.2, 0.2, 0.6, 0.18))) is not None
        ][:4]
        text_layer = [
            item
            for idx, raw in enumerate(payload.get("text_layer", []))
            if (item := _coerce_element(raw, element_id=f"text-layer:{idx}", kind="text_layer", role="evidence", fallback_bbox=(0.18, 0.2, 0.64, 0.2))) is not None
        ][:4]
        text_regions = [
            item
            for idx, raw in enumerate(payload.get("text_regions", []))
            if (item := _coerce_element(raw, element_id=f"text:{idx}", kind="text_region", role="evidence", fallback_bbox=(0.18, 0.2, 0.64, 0.2))) is not None
        ][:4]
        object_layer = [
            item
            for idx, raw in enumerate(payload.get("object_layer", []))
            if (item := _coerce_element(raw, element_id=f"object:{idx}", kind="object_cluster", role="workflow_anchor", fallback_bbox=(0.2, 0.4, 0.6, 0.3))) is not None
        ][:4]
        action_controls = [
            item
            for idx, raw in enumerate(payload.get("action_controls", []))
            if (item := _coerce_element(raw, element_id=f"control:{idx}", kind="action_control", role="action_target", fallback_bbox=(0.22, 0.58, 0.56, 0.24))) is not None
        ][:4]
        hazard_layer = [
            item
            for idx, raw in enumerate(payload.get("hazard_layer", []))
            if (item := _coerce_element(raw, element_id=f"hazard-layer:{idx}", kind="hazard_layer", role="risk_signal", fallback_bbox=(0.14, 0.08, 0.72, 0.16))) is not None
        ][:4]
        hazard_cues = [
            item
            for idx, raw in enumerate(payload.get("hazard_cues", []))
            if (item := _coerce_element(raw, element_id=f"hazard:{idx}", kind="hazard_cue", role="risk_signal", fallback_bbox=(0.14, 0.08, 0.72, 0.16))) is not None
        ][:4]
        attention_targets = [
            item
            for idx, raw in enumerate(payload.get("attention_targets", []))
            if (item := _coerce_element(raw, element_id=f"attention:{idx}", kind="attention_target", role="attention_target", fallback_bbox=(0.18, 0.18, 0.64, 0.24))) is not None
        ][:4]
        evidence_gaps = [
            EvidenceGap(
                gap_type=str(item.get("gap_type") or "unknown"),
                reason=str(item.get("reason") or "").strip() or "The provider reported missing evidence.",
                suggested_follow_up=str(item.get("suggested_follow_up") or "").strip() or "Capture a clearer view before deciding.",
            )
            for item in payload.get("evidence_gaps", [])
            if isinstance(item, dict)
        ][:4]
        return SceneObservation(
            summary=str(payload.get("summary", "")).strip() or "No scene summary returned.",
            risk_level=_coerce_risk(str(payload.get("risk_level", "low"))),
            tags=[str(item) for item in payload.get("tags", []) if str(item).strip()][:8],
            provider=self.name,
            structure=SceneStructure(
                layout_summary=str(payload.get("layout_summary", "")).strip(),
                workflow_state=str(payload.get("workflow_state", "observe_context")).strip() or "observe_context",
                workflow_reason=str(payload.get("workflow_reason", "")).strip(),
                temporal_delta_summary=str(payload.get("temporal_delta_summary", "")).strip(),
                attention_summary=str(payload.get("attention_summary", "")).strip(),
                text_layer=text_layer or text_regions[:2],
                object_layer=object_layer or action_controls[:2] or primary_entry_points[:2],
                hazard_layer=hazard_layer or hazard_cues[:2],
                attention_targets=attention_targets or hazard_layer[:1] or text_layer[:1] or object_layer[:1],
                state_transitions=[str(item).strip() for item in payload.get("state_transitions", []) if str(item).strip()][:6],
                primary_entry_points=primary_entry_points,
                text_regions=text_regions,
                action_controls=action_controls,
                hazard_cues=hazard_cues,
                salient_elements=(attention_targets[:1] or text_regions[:1] or action_controls[:1] or primary_entry_points[:1] or hazard_cues[:1]),
            ),
            uncertainty_level=str(payload.get("uncertainty_level", "medium")).strip().lower() or "medium",
            evidence_gaps=evidence_gaps,
        )


class AnthropicDecisionProvider(_AnthropicBaseProvider):
    def __init__(self) -> None:
        super().__init__(ANTHROPIC_DECISION_MODEL)

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
        docs = [
            {
                "title": hit.title,
                "snippet": hit.snippet,
                "score": hit.score,
            }
            for hit in retrieved_docs[:5]
        ]
        payload = await self._json_completion(
            prompt=(
                "You are the decision layer for a wearable scene assistant. "
                "Return strict JSON with keys "
                '{"title": string, "recommendation": string, "risk_level": "low"|"medium"|"high", '
                '"next_steps": string[], "confidence": number, "priority": "low"|"medium"|"high", '
                '"intervention_type": "wait"|"answer"|"ask_clarification"|"recommend_action"|"require_approval"|"lightweight_offer"}. '
                f"User prompt: {prompt}\n"
                f"Scene summary: {scene_summary}\n"
                f"OCR text: {ocr_text}\n"
                f"Scene structure: {json.dumps({'layout_summary': (scene_structure.layout_summary if scene_structure else ''), 'workflow_state': (scene_structure.workflow_state if scene_structure else ''), 'attention_summary': (scene_structure.attention_summary if scene_structure else ''), 'text_layer': [item.label for item in (scene_structure.text_layer if scene_structure else [])], 'object_layer': [item.label for item in (scene_structure.object_layer if scene_structure else [])], 'hazard_layer': [item.label for item in (scene_structure.hazard_layer if scene_structure else [])], 'attention_targets': [item.label for item in (scene_structure.attention_targets if scene_structure else [])]}, ensure_ascii=True)}\n"
                f"Recent memory: {memory_context}\n"
                f"Retrieved docs: {json.dumps(docs, ensure_ascii=True)}"
            ),
        )
        intervention_raw = str(payload.get("intervention_type", InterventionType.RECOMMEND_ACTION.value)).strip().lower()
        try:
            intervention_type = InterventionType(intervention_raw)
        except ValueError:
            intervention_type = InterventionType.RECOMMEND_ACTION
        return ActionRecommendation(
            title=str(payload.get("title", "")).strip() or "SceneCopilot recommendation",
            recommendation=str(payload.get("recommendation", "")).strip() or "No recommendation returned.",
            risk_level=_coerce_risk(str(payload.get("risk_level", "low"))),
            next_steps=[str(item) for item in payload.get("next_steps", []) if str(item).strip()][:6],
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            priority=str(payload.get("priority", "medium")).strip().lower() or "medium",
            intervention_type=intervention_type,
            supporting_doc_titles=[item["title"] for item in docs[:4]],
        )
