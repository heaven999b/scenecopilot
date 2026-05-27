# SceneCopilot System Blueprint

SceneCopilot should evolve from a demo app into a long-lived multimodal agent
platform. This document defines the target architecture.

It is inspired by the public behavior of strong agentic runtimes such as
Claude Code, but it does **not** claim to reproduce any internal proprietary
implementation. The goal is a similarly disciplined architecture:

- a clear runtime kernel
- explicit session and run state
- pluggable tools and providers
- durable artifacts and memory
- bounded execution and approvals
- strong observability

## Product goal

SceneCopilot is a real-time scene assistant for wearable and mobile devices.
It should support:

- first-person camera capture
- scene understanding and OCR
- spoken and typed interaction
- document lookup across manuals, SOPs, and user knowledge bases
- decision support with explicit safety handling
- human approval for high-risk or external actions
- resumable sessions with streamed intermediate state

## Target repo shape

```text
scenecopilot/
├── backend/
│   └── app/
│       ├── api/                  FastAPI route layer
│       ├── orchestration/        planner, policies, run state machine
│       ├── domain/               runtime models and contracts
│       ├── services/             retrieval, session, ingest, approvals
│       ├── providers/            OCR/VLM/ASR/TTS/vector/provider adapters
│       ├── observability/        metrics, traces, audit records
│       ├── agent/                compatibility layer during migration
│       ├── db.py                 storage primitives
│       └── main.py               application bootstrap
├── frontend-android/
│   └── app/
│       ├── capture/              camera, audio, wearable bridge
│       ├── session/              live run state, SSE transport
│       ├── ui/                   activity, fragments, adapters
│       ├── playback/             TTS and media feedback
│       └── storage/              offline queue and local cache
└── docs/
    ├── system-blueprint.md
    ├── implementation-roadmap.md
    └── architecture.md
```

## Core backend layers

### 1. API layer

Responsibilities:

- validate input
- authenticate users and devices
- enforce rate limits and payload limits
- submit work to the runtime
- expose health, metrics, and admin views

Target endpoints:

- `POST /api/chat`
- `POST /api/scans/analyze`
- `POST /api/audio/transcribe`
- `POST /api/documents/upload`
- `GET /api/events/{session_id}`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/approve`
- `GET /api/system/metrics`

### 2. Runtime kernel

This is the heart of the project and should feel closer to an agent platform
than a single monolithic request handler.

Responsibilities:

- create and track runs
- maintain a run state machine
- manage bounded concurrency
- stream stage and tool events
- enforce approval boundaries
- resume or replay prior runs

Primary concepts:

- `Session`: long-lived conversation and artifact scope
- `Run`: one request execution inside a session
- `Plan`: ordered steps chosen for this run
- `Stage`: routing, OCR, scene analysis, retrieval, decision, persistence
- `Artifact`: image, audio, OCR text, transcript, retrieved doc, final answer

### 3. Orchestration layer

Responsibilities:

- determine the cheapest viable plan
- select tools and providers
- decide whether retrieval is needed
- determine whether the run can auto-complete or needs approval

Rules should be explicit and testable. Avoid burying critical policy inside
prompt text alone.

### 4. Service layer

Responsibilities:

- session management
- retrieval orchestration
- ingest pipelines
- approval workflows
- artifact persistence
- local cache coordination

This layer should contain business logic that is independent from HTTP and
independent from any single model provider.

### 5. Provider adapter layer

Responsibilities:

- OCR provider
- vision-language provider
- speech-to-text provider
- text-to-speech provider
- embedding / vector index provider
- external actions provider

Every provider should be behind an interface so the runtime can swap vendors,
run canaries, or fall back when a provider is degraded.

### 6. Observability layer

Responsibilities:

- run-level audit log
- latency histograms
- queue depth metrics
- tool success/failure counts
- provider-specific error rates
- replayable event stream

In a production version, each run should have:

- `session_id`
- `run_id`
- `request_id`
- `device_id`
- `user_id`

## Runtime sequence

```text
Client capture
  -> API validates payload
  -> SessionRegistry creates or resumes session
  -> RunScheduler accepts or rejects based on capacity
  -> RunStateMachine enters QUEUED
  -> Planner chooses low-latency execution plan
  -> Providers execute OCR / scene / retrieval / synthesis
  -> Policy engine determines whether approval is needed
  -> Artifact store persists outputs
  -> Event bus streams progress to client
  -> RunStateMachine enters COMPLETED or NEEDS_APPROVAL or FAILED
```

## Recommended run states

- `QUEUED`
- `STARTING`
- `CAPTURING_CONTEXT`
- `RUNNING_OCR`
- `RUNNING_VISION`
- `RUNNING_RETRIEVAL`
- `SYNTHESIZING`
- `WAITING_FOR_APPROVAL`
- `PERSISTING`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

## Latency strategy

Real-time interaction matters more than maximal reasoning depth on every turn.

### Fast path

Use the fast path when the user asks simple things such as:

- read this sign
- summarize this label
- what am I looking at

Fast path behavior:

- lightweight OCR
- lightweight scene classifier
- retrieval only if confidence is low or user asks for procedure
- early streaming within hundreds of milliseconds

### Heavy path

Use the heavy path when the user asks:

- what should I do next
- compare this to the manual
- identify hazards
- draft a formal recommendation

Heavy path behavior:

- OCR + VLM
- hybrid retrieval
- policy checks
- richer final synthesis

### Tail latency controls

- per-tool timeout
- queue admission control
- cancellation propagation
- cached retrieval for repeated prompts
- staged provider fallback

## Data and storage model

### Durable data

- users
- devices
- sessions
- runs
- reasoning events
- artifacts
- documents
- document chunks
- embeddings
- approvals
- action cards

### Artifact store

Artifacts should be stored explicitly rather than only embedded in chat logs:

- source image
- audio clip
- OCR output
- scene summary
- retrieved documents
- final answer
- optional TTS audio

## Android Java client architecture

The mobile app should not stay as one activity forever.

Recommended structure:

- `capture/`: CameraX, audio recording, wearable bridge receiver
- `session/`: SSE client, retry, local run store, offline queue
- `domain/`: session models mirrored from backend
- `ui/home/`: current session and action cards
- `ui/live/`: stage-by-stage run stream
- `ui/docs/`: document search and reading
- `playback/`: TTS and audio feedback

## Security and policy

Production maturity requires explicit controls:

- signed device registration
- per-user auth tokens
- upload scanning and file type validation
- approval gate before external actions
- audit trail for every run and action
- configurable retention for images and transcripts

## Deployment shape

For a more serious deployment:

- API service
- worker service
- vector index / retrieval service
- relational DB
- object storage
- optional Redis for queueing and cache

The current repo can stay monolithic while preserving these boundaries in code.

## Migration rule

Do not rewrite everything at once.

Move incrementally:

1. keep current demo paths working
2. add runtime contracts and services
3. migrate routes to the new services
4. phase out compatibility code after parity is reached
