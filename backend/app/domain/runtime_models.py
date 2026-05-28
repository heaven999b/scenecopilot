from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class RunStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    CAPTURING_CONTEXT = "capturing_context"
    RUNNING_ASR = "running_asr"
    RUNNING_OCR = "running_ocr"
    RUNNING_VISION = "running_vision"
    RUNNING_RETRIEVAL = "running_retrieval"
    SYNTHESIZING = "synthesizing"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PERSISTING = "persisting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PlanStepType(StrEnum):
    ASR = "asr"
    OCR = "ocr"
    VISION = "vision"
    RETRIEVAL = "retrieval"
    DECISION = "decision"
    APPROVAL = "approval"
    PERSIST = "persist"


class ArtifactType(StrEnum):
    OCR = "ocr_artifact"
    SCENE = "scene_observation"
    RETRIEVAL = "retrieval_hits"
    DECISION = "action_recommendation"
    APPROVAL = "approval_record"
    TRANSCRIPT = "transcript"
    EMBEDDING = "embedding"
    ALIGNMENT = "temporal_alignment"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(slots=True)
class FrameRef:
    artifact_id: str
    uri: str
    mime_type: str
    width: int | None = None
    height: int | None = None


@dataclass(slots=True)
class OCRBlock:
    text: str
    confidence: float | None = None


@dataclass(slots=True)
class OCRResult:
    text: str
    blocks: list[OCRBlock] = field(default_factory=list)
    provider: str = "unknown"


@dataclass(slots=True)
class SceneObservation:
    summary: str
    risk_level: RiskLevel
    tags: list[str] = field(default_factory=list)
    provider: str = "unknown"


@dataclass(slots=True)
class RetrievalHit:
    document_id: int | str
    title: str
    snippet: str
    score: float
    source: str


@dataclass(slots=True)
class ActionRecommendation:
    title: str
    recommendation: str
    risk_level: RiskLevel
    next_steps: list[str] = field(default_factory=list)
    confidence: float | None = None
    priority: str = "medium"
    blocked: bool = False
    approval_required: bool = False


@dataclass(slots=True)
class ArtifactRecord:
    artifact_type: ArtifactType
    stage: str
    provider: str
    content: dict[str, object]


@dataclass(slots=True)
class ApprovalRecord:
    status: ApprovalStatus
    risk_level: RiskLevel
    policy_name: str
    reason: str
    recommended_action: str


@dataclass(slots=True)
class PlanStep:
    step_type: PlanStepType
    required: bool = True
    rationale: str = ""


@dataclass(slots=True)
class ExecutionPlan:
    route_name: str
    modalities: list[Modality]
    steps: list[PlanStep] = field(default_factory=list)


@dataclass(slots=True)
class RunSnapshot:
    session_id: str
    run_id: str
    status: RunStatus
    user_message: str
    plan: ExecutionPlan
