from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class RunStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    AWAITING_INPUT = "awaiting_input"
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


class InterventionType(StrEnum):
    WAIT = "wait"
    ANSWER = "answer"
    ASK_CLARIFICATION = "ask_clarification"
    RECOMMEND_ACTION = "recommend_action"
    REQUIRE_APPROVAL = "require_approval"
    LIGHTWEIGHT_OFFER = "lightweight_offer"


class MemoryScope(StrEnum):
    RUN = "run"
    SESSION = "session"
    SCENE_CHANGE = "scene_change"
    USER_CHOICE = "user_choice"


class PlanStepType(StrEnum):
    ASR = "asr"
    OCR = "ocr"
    VISION = "vision"
    RETRIEVAL = "retrieval"
    DECISION = "decision"
    APPROVAL = "approval"
    PERSIST = "persist"


class ArtifactType(StrEnum):
    FRAME_WINDOW = "frame_window"
    OCR = "ocr_artifact"
    SCENE = "scene_observation"
    RETRIEVAL = "retrieval_hits"
    GROUNDING = "scene_action_grounding"
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
class SceneElement:
    element_id: str
    kind: str
    label: str
    salience: str = "medium"
    role: str = "context"
    evidence: str | None = None
    bbox_x: float | None = None
    bbox_y: float | None = None
    bbox_w: float | None = None
    bbox_h: float | None = None


@dataclass(slots=True)
class EvidenceGap:
    gap_type: str
    reason: str
    suggested_follow_up: str


@dataclass(slots=True)
class SceneStructure:
    layout_summary: str = ""
    primary_entry_points: list[SceneElement] = field(default_factory=list)
    text_regions: list[SceneElement] = field(default_factory=list)
    action_controls: list[SceneElement] = field(default_factory=list)
    hazard_cues: list[SceneElement] = field(default_factory=list)
    overlays: list[SceneElement] = field(default_factory=list)
    salient_elements: list[SceneElement] = field(default_factory=list)


@dataclass(slots=True)
class SceneObservation:
    summary: str
    risk_level: RiskLevel
    tags: list[str] = field(default_factory=list)
    provider: str = "unknown"
    structure: SceneStructure = field(default_factory=SceneStructure)
    uncertainty_level: str = "low"
    evidence_gaps: list[EvidenceGap] = field(default_factory=list)


@dataclass(slots=True)
class RetrievalHit:
    document_id: int | str
    title: str
    snippet: str
    score: float
    source: str


@dataclass(slots=True)
class GroundingReference:
    anchor_type: str
    anchor_label: str
    action_step: str
    rationale: str
    doc_title: str | None = None
    support_snippet: str | None = None
    confidence: float | None = None


@dataclass(slots=True)
class ChoiceOption:
    option_id: str
    label: str
    description: str
    requires_confirmation: bool = False


@dataclass(slots=True)
class ChoiceCard:
    card_type: str
    headline: str
    rationale: str
    options: list[ChoiceOption] = field(default_factory=list)
    evidence_hint: str | None = None
    cancellable: bool = True
    deferrable: bool = True


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
    intervention_type: InterventionType = InterventionType.RECOMMEND_ACTION
    uncertainty_level: str = "low"
    clarification_question: str | None = None
    evidence_supported: bool = True
    supporting_doc_titles: list[str] = field(default_factory=list)
    grounding_refs: list[GroundingReference] = field(default_factory=list)
    choice_card: ChoiceCard | None = None


@dataclass(slots=True)
class MemoryLayers:
    run_memory: dict[str, Any] = field(default_factory=dict)
    session_memory: dict[str, Any] = field(default_factory=dict)
    scene_change_memory: dict[str, Any] = field(default_factory=dict)
    user_choice_memory: dict[str, Any] = field(default_factory=dict)


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
    packet: dict[str, Any] = field(default_factory=dict)


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
