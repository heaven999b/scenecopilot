# SceneCopilot

SceneCopilot is a wearable-first scene assistant for real-time inspection,
text reading, and next-step decision support.

The project is organized around a multimodal runtime stack:

- `backend/`: FastAPI + Python agent loop + SQLite + SSE event stream
- `frontend-android/`: Android Java companion app for camera/gallery input
- `docs/`: product and architecture notes

## Product shape

SceneCopilot is built for glasses, phones, or other first-person cameras.
It can:

- scan a scene and summarize what matters
- read visible text and extract key instructions
- search uploaded manuals and SOPs for matching guidance
- suggest the safest or most likely next action
- stream its reasoning and tool activity back to the UI

## Monorepo layout

```text
scenecopilot/
├── README.md
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── seed.py
│   │   ├── agent/
│   │   ├── ingest/
│   │   └── routes/
│   └── data/
│       ├── seed/
│       ├── uploads/
│       └── watched/
├── frontend-android/
│   ├── settings.gradle
│   ├── build.gradle
│   └── app/
└── docs/
    ├── architecture.md
    ├── system-blueprint.md
    └── implementation-roadmap.md
```

## Backend quick start

Python 3.11+ is enough. `uv` is optional.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp ../.env.example .env
python -m app.seed
uvicorn app.main:app --reload --port 8002
```

Backend routes:

- `GET /`
- `GET /dashboard`
- `GET /api/health`
- `POST /api/chat`
- `POST /api/scans/analyze`
- `GET /api/events/{session_id}`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/approve`
- `POST /api/documents/upload`
- `GET /api/documents/search?q=...`
- `GET /api/dashboard/summary`
- `GET /api/system/metrics`
- `GET /api/state`

## Production-minded backend upgrades

This version includes a more mature execution path than the initial scaffold:

- bounded async run scheduler with queue backpressure
- durable `sessions` and `runs` records with `run_id` on responses and events
- explicit `queued` and `run_started` SSE events
- per-run status progression from queued to completed or failed
- explicit `planner -> policy -> services -> providers` execution layering
- code-level OCR, retrieval, and approval policies instead of prompt-only routing
- replaceable OCR, vision, speech, retrieval, embedding, and decision providers
- optional Anthropic-backed OCR, vision, and decision providers with local fallback
- hybrid document retrieval with chunking, hashed local embeddings, and FTS reranking
- optional external search enrichment for explicit document searches
- durable OCR / scene / retrieval / decision / approval artifacts per run
- run-level audit trail plus scene memory and action-card persistence
- explicit approval-resolution API that moves blocked runs to approved or rejected
- warm document retrieval in parallel with OCR/scene steps
- SQLite WAL mode plus FTS-backed document search
- short-lived in-memory search cache for repeated queries
- upload size limits for images and documents
- `X-Process-Time-Ms` response header for quick latency inspection
- `/api/system/metrics` for scheduler and event-bus visibility
- run-scoped SSE filtering so clients can subscribe to one execution inside a session

## Mature-project direction

The current implementation is still a compact prototype. The repo now also
includes a larger-scale architecture path:

- [System Blueprint](./docs/system-blueprint.md)
- [Implementation Roadmap](./docs/implementation-roadmap.md)

Those documents define the target runtime kernel, service boundaries,
provider interfaces, run/session model, and migration steps toward a more
serious multimodal agent platform.

## Evaluation baseline

The backend now includes a small evaluation harness so quality is not judged
only by “it runs”:

```bash
cd backend
python3 -m app.evals.harness
```

The harness currently reports:

- OCR accuracy
- retrieval hit rate
- high-risk miss rate
- average and p95 latency
- provider fallback success rate

It uses seeded fixture scenes under `backend/data/evals/` and records results
through the same run + artifact pipeline as normal execution.

## Browser Workspace

The repo now includes a browser control deck at `/dashboard` to make the
project easier to inspect and demo:

- launch text or image runs
- inspect recent runs, approvals, artifacts, and audit trails
- upload documents and test retrieval
- subscribe to a single run via SSE
- approve or reject blocked runs from the browser

This is a management surface. The primary field client is still the Android
Java app.

## Android quick start

The frontend is an Android Java companion app. It assumes:

- emulator uses `http://10.0.2.2:8002/`
- physical devices should change the base URL in `ApiClient.java`

Open `frontend-android/` in Android Studio and run the `app` module.

The Android app now includes:

- live SSE event stream
- run detail view with route, artifacts, approvals, and recent audit events
- approval controls for runs that stop at `waiting_for_approval`
- document search and TTS playback
- direct camera capture for on-the-spot scene analysis

## Demo flow

1. Upload a manual or SOP into the backend.
2. Pick a scene image from the Android app.
3. Ask for one of:
   - `Read the visible text`
   - `What am I looking at?`
   - `What should I do next?`
4. Watch the SSE reasoning stream update in real time.
5. Let the app read the final answer aloud with Android TTS.
6. Open `/dashboard` in a browser to review the run, artifacts, and approvals.

## Notes

- The backend includes a provider-ready agent structure, but it also runs in a
  local stub mode so the repo stays demo-friendly without model keys.
- To try the higher-fidelity path, set `SCENECOPILOT_OCR_PROVIDER=anthropic`,
  `SCENECOPILOT_VISION_PROVIDER=anthropic`, and/or
  `SCENECOPILOT_DECISION_PROVIDER=anthropic` with a valid `ANTHROPIC_API_KEY`.
- The Java frontend is intentionally a phone companion app. That is the
  cleanest interpretation of "frontend in Java" for wearable input workflows.
- The current workspace did not have FastAPI installed globally, so backend
  server startup was validated at the code level and with direct module runs,
  but not by launching `uvicorn` inside this session.
