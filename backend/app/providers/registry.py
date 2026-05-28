from __future__ import annotations

from dataclasses import dataclass

from ..config import (
    DECISION_PROVIDER,
    EMBEDDING_PROVIDER,
    OCR_PROVIDER,
    RETRIEVAL_PROVIDER,
    SPEECH_PROVIDER,
    VISION_PROVIDER,
)
from .local import (
    LocalHashEmbeddingProvider,
    LocalDecisionProvider,
    LocalOCRProvider,
    LocalSpeechProvider,
    LocalVisionProvider,
    NoopEmbeddingProvider,
    NoopSpeechProvider,
    SQLiteRetrievalProvider,
)


@dataclass(slots=True)
class ProviderBundle:
    ocr: list[object]
    vision: list[object]
    retrieval: list[object]
    decision: list[object]
    speech: list[object]
    embedding: list[object]


def _select(name: str, mapping: dict[str, object], fallback: object) -> list[object]:
    provider = mapping.get(name, fallback)
    if provider is fallback:
        return [fallback]
    return [provider, fallback] if provider is not fallback else [fallback]


def load_provider_bundle() -> ProviderBundle:
    local_ocr = LocalOCRProvider()
    local_vision = LocalVisionProvider()
    sqlite_retrieval = SQLiteRetrievalProvider()
    local_decision = LocalDecisionProvider()
    local_embedding = LocalHashEmbeddingProvider()
    local_speech = LocalSpeechProvider()
    noop_speech = NoopSpeechProvider()
    noop_embedding = NoopEmbeddingProvider()
    anthropic_ocr = local_ocr
    anthropic_vision = local_vision
    anthropic_decision = local_decision
    openai_speech = local_speech

    try:
        from .anthropic import AnthropicDecisionProvider, AnthropicOCRProvider, AnthropicVisionProvider

        anthropic_ocr = AnthropicOCRProvider()
        anthropic_vision = AnthropicVisionProvider()
        anthropic_decision = AnthropicDecisionProvider()
    except Exception:
        pass

    try:
        from .openai_audio import OpenAITranscriptionProvider

        openai_speech = OpenAITranscriptionProvider()
    except Exception:
        openai_speech = local_speech

    return ProviderBundle(
        ocr=_select(OCR_PROVIDER, {"local": local_ocr, "anthropic": anthropic_ocr}, local_ocr),
        vision=_select(VISION_PROVIDER, {"local": local_vision, "anthropic": anthropic_vision}, local_vision),
        retrieval=_select(RETRIEVAL_PROVIDER, {"sqlite": sqlite_retrieval}, sqlite_retrieval),
        decision=_select(DECISION_PROVIDER, {"local": local_decision, "anthropic": anthropic_decision}, local_decision),
        speech=_select(
            SPEECH_PROVIDER,
            {"local": local_speech, "openai": openai_speech, "noop": noop_speech},
            local_speech,
        ),
        embedding=_select(EMBEDDING_PROVIDER, {"local_hash": local_embedding, "noop": noop_embedding}, local_embedding),
    )


provider_bundle = load_provider_bundle()
