# SceneCopilot Implementation Roadmap

This is the recommended path from the current demo scaffold to a mature
project.

## Phase 0: Foundation hardening

Goal: make the current code safe to grow.

Deliverables:

- typed runtime contracts
- explicit run and session models
- provider interfaces
- route layer separated from orchestration logic
- integration tests for current scan/chat flows

## Phase 1: Session and run model

Goal: stop treating every request like a standalone ad hoc task.

Deliverables:

- durable `sessions` table
- durable `runs` table
- `run_id` in every response and event
- resumable event playback
- run state machine
- per-run audit record

## Phase 2: Multimodal pipeline separation

Goal: split capture, OCR, retrieval, and decision into composable services.

Deliverables:

- `ScenePipelineService`
- `OCRService`
- `RetrievalService`
- `DecisionService`
- `ArtifactStore`
- test fixtures for image/text/audio artifacts

## Phase 3: Provider abstraction

Goal: support multiple vendors and fallback behavior.

Deliverables:

- OCR provider interface
- vision provider interface
- ASR provider interface
- embedding provider interface
- runtime provider selection config
- provider health and fallback policy

## Phase 4: Retrieval maturity

Goal: move from simple document lookup to serious hybrid retrieval.

Deliverables:

- document chunking pipeline
- embedding generation
- hybrid lexical plus semantic retrieval
- document ingestion jobs
- retrieval quality evaluation set

## Phase 5: Approval and action workflows

Goal: support higher-stakes decisions and external actions.

Deliverables:

- approval state machine
- action proposal model
- approval inbox
- callback endpoints for approved or rejected actions

## Phase 6: Android app maturity

Goal: turn the Java client into a real mobile product.

Deliverables:

- CameraX capture module
- background upload queue
- SSE reconnect policy
- local artifact cache
- multiple screens instead of one activity
- wearable bridge integration

## Phase 7: Reliability and ops

Goal: make behavior measurable and resilient.

Deliverables:

- structured logs
- run metrics dashboard
- saturation alerts
- retry policy by provider type
- graceful degradation modes
- migration and backup strategy

## Phase 8: Security and release

Goal: ship beyond localhost.

Deliverables:

- auth and device registration
- secrets handling
- signed builds
- rate limits by account and device
- data retention controls
- production deployment manifests

## Immediate next coding steps

These are the best next engineering moves inside the current repo:

1. add `session_id` plus `run_id` domain objects and persistence
2. create orchestration contracts and state enums
3. split current `agent/core.py` logic into planner and services
4. add provider abstraction layer even if the first implementation is local
5. break the Android app into `capture`, `session`, and `ui` packages

## Definition of maturity

SceneCopilot should be considered release-grade at this layer when it has:

- clear module boundaries
- stable runtime contracts
- reproducible test data
- provider pluggability
- bounded concurrency
- explicit approvals
- resumable sessions
- measurable latency and failure rates
