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
                '"layout_summary": string, "primary_entry_points": string[], "hazard_cues": string[], '
                '"evidence_gaps": [{"gap_type": string, "reason": string, "suggested_follow_up": string}]}. '
                f"User prompt: {prompt}\nOCR text: {ocr_text}"
            ),
        )
        primary_entry_points = [
            SceneElement(
                element_id=f"entry:{idx}",
                kind="primary_entry",
                label=str(item).strip(),
                salience="high",
                role="entry_point",
            )
            for idx, item in enumerate(payload.get("primary_entry_points", []))
            if str(item).strip()
        ][:4]
        hazard_cues = [
            SceneElement(
                element_id=f"hazard:{idx}",
                kind="hazard_cue",
                label=str(item).strip(),
                salience="high",
                role="risk_signal",
            )
            for idx, item in enumerate(payload.get("hazard_cues", []))
            if str(item).strip()
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
                primary_entry_points=primary_entry_points,
                hazard_cues=hazard_cues,
                salient_elements=primary_entry_points[:1] or hazard_cues[:1],
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
                f"Scene structure: {json.dumps({'layout_summary': (scene_structure.layout_summary if scene_structure else ''), 'primary_entry_points': [item.label for item in (scene_structure.primary_entry_points if scene_structure else [])], 'hazard_cues': [item.label for item in (scene_structure.hazard_cues if scene_structure else [])]}, ensure_ascii=True)}\n"
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
