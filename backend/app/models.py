from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    image_paths: list[str] | None = None
    audio_paths: list[str] | None = None
    use_latest_frame: bool = False


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
    timings_json: dict[str, Any] | None = None
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
    continuation_run_id: str | None = None
    continuation_queue_position: int | None = None


class RunCancelResponse(BaseModel):
    run_id: str
    status: str
    cancelled: bool = True


class RunRetryResponse(BaseModel):
    session_id: str
    run_id: str
    source_run_id: str
    accepted: bool = True
    state: str = "queued"
    queue_position: int = 0


class RunReplayResponse(BaseModel):
    run_id: str
    session_id: str
    status: str
    current_stage: str | None = None
    event_count: int = 0
    latest_event_id: int | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    timings_json: dict[str, Any] | None = None


class RunContinueResponse(BaseModel):
    session_id: str
    run_id: str
    accepted: bool = True
    state: str = "queued"
    queue_position: int = 0


class ActionCardExecuteRequest(BaseModel):
    option_id: str
    note: str | None = None


class ActionCardExecuteResponse(BaseModel):
    card_id: int
    run_id: str
    option_id: str
    status: str
    accepted: bool = True
    message: str
    continuation_run_id: str | None = None
    continuation_queue_position: int | None = None
    continuation_state: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class ClientIncidentRequest(BaseModel):
    session_id: str
    incident_type: str = Field(
        pattern="^(network_drop|weak_network|camera_failure|camera_permission_denied|microphone_failure|microphone_permission_denied|tts_interrupted|backgrounded|upload_failed|stream_reconnect)$"
    )
    run_id: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ClientIncidentResponse(BaseModel):
    accepted: bool = True
    incident_type: str
    session_id: str
    run_id: str | None = None


class DeviceRegisterRequest(BaseModel):
    display_name: str
    platform: str | None = None
    client_version: str | None = None


class DeviceRegisterResponse(BaseModel):
    device_id: str
    device_token: str
    auth_mode: str
    cloud_mode_enabled: bool
    data_retention_days: int


class DeviceListResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class SecurityProfileResponse(BaseModel):
    auth_mode: str
    auth_required: bool
    open_device_registration: bool
    cloud_mode_enabled: bool
    data_retention_days: int
    device_token_ttl_days: int


class SystemMetricsResponse(BaseModel):
    scheduler: dict[str, int]
    event_bus: dict[str, int]
    frame_stash: dict[str, int]
    watcher: dict[str, int]
    scan_aggregator: dict[str, int]
    media_lifecycle: dict[str, int]
