from __future__ import annotations

from pathlib import Path

from ..agent.tools import docs as docs_tool
from ..domain.runtime_models import (
    ActionRecommendation,
    FrameRef,
    OCRBlock,
    OCRResult,
    RetrievalHit,
    RiskLevel,
    SceneObservation,
)


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
        return SceneObservation(
            summary=summary,
            risk_level=_classify_risk(context),
            tags=_keywords(context),
            provider=self.name,
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
    ) -> ActionRecommendation:
        lower = f"{prompt} {scene_summary} {ocr_text}".lower()
        risk_level = _classify_risk(lower)
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
            )
        recommendation = "Use the visible cues and any matching documents to confirm the object or situation before taking the next step."
        if retrieved_docs:
            recommendation += f" I found {len(retrieved_docs)} relevant document(s) to cross-check."
        return ActionRecommendation(
            title="Inspect, confirm, then act",
            recommendation=recommendation,
            risk_level=risk_level,
            next_steps=[
                "Identify the object, label, or panel in view.",
                "Confirm any matching instructions from uploaded documents.",
                "Proceed with the lowest-risk next action and keep capturing context if needed.",
            ],
            confidence=0.73 if retrieved_docs or ocr_text else 0.58,
            priority="medium",
        )


class NoopSpeechProvider:
    name = "noop"

    async def transcribe(self, audio_path: str) -> str:
        return f"Speech provider not configured for {audio_path}."


class LocalHashEmbeddingProvider:
    name = "local_hash"

    async def embed(self, text: str) -> list[float]:
        return docs_tool.embed_text(text)


class NoopEmbeddingProvider:
    name = "noop"

    async def embed(self, text: str) -> list[float]:
        return []
