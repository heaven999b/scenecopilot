from __future__ import annotations

from typing import Protocol

from ..domain.runtime_models import (
    ActionRecommendation,
    FrameRef,
    OCRResult,
    RetrievalHit,
    RiskLevel,
    SceneObservation,
)


class OCRProvider(Protocol):
    async def extract_text(self, frame: FrameRef) -> OCRResult:
        ...


class VisionProvider(Protocol):
    async def analyze_scene(self, frame: FrameRef, prompt: str, ocr_text: str = "") -> SceneObservation:
        ...


class RetrievalProvider(Protocol):
    async def search(self, query: str, limit: int = 5) -> list[RetrievalHit]:
        ...


class DecisionProvider(Protocol):
    async def recommend(
        self,
        *,
        prompt: str,
        scene_summary: str,
        ocr_text: str,
        retrieved_docs: list[RetrievalHit],
    ) -> ActionRecommendation:
        ...


class SpeechProvider(Protocol):
    async def transcribe(self, audio_path: str) -> str:
        ...


class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]:
        ...


class ApprovalPolicy(Protocol):
    def needs_approval(self, risk_level: RiskLevel) -> bool:
        ...
