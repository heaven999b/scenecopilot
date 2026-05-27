from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    image_paths: list[str] | None = None
    audio_paths: list[str] | None = None


class ChatResponse(BaseModel):
    session_id: str
    run_id: str
    accepted: bool = True
    state: str = "queued"
    queue_position: int = 0


class AudioChunkUploadResponse(BaseModel):
    accepted: bool = True
    upload_id: str
    session_id: str
    received_chunk: int
    finalized: bool = False
    state: str = "uploading"
    run_id: str | None = None
    queue_position: int | None = None


class DocumentUploadResponse(BaseModel):
    document_id: int
    title: str
    accepted: bool = True


class DocumentSearchItem(BaseModel):
    id: int | str
    title: str
    summary: str | None = None
    snippet: str | None = None
    score: float = 0
    source_path: str | None = None
    source: str | None = None


class DocumentSearchResponse(BaseModel):
    query: str
    items: list[DocumentSearchItem] = Field(default_factory=list)


class StateResponse(BaseModel):
    documents: list[dict[str, Any]]
    recent_captures: list[dict[str, Any]]
    action_cards: list[dict[str, Any]]
    recent_runs: list[dict[str, Any]]


class RunDetailResponse(BaseModel):
    id: str
    session_id: str
    status: str
    trigger: str
    user_message: str
    route_name: str | None = None
    image_count: int = 0
    queue_position: int | None = None
    current_stage: str | None = None
    output_text: str | None = None
    latency_ms: float | None = None
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    input_json: dict[str, Any] | None = None
    plan_json: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    audit_log: list[dict[str, Any]] = Field(default_factory=list)
    scene_captures: list[dict[str, Any]] = Field(default_factory=list)
    action_cards: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reviewer_note: str | None = None


class RunApprovalResponse(BaseModel):
    run_id: str
    status: str
    approval_status: str
    reviewer_note: str | None = None


class SystemMetricsResponse(BaseModel):
    scheduler: dict[str, int]
    event_bus: dict[str, int]
