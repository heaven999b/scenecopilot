# SceneCopilot Architecture

SceneCopilot is a wearable-first multimodal system for scene inspection,
OCR, document-grounded guidance, and approval-aware decision support.

## Core modules

- `documents` store manuals, SOPs, and reference guides
- `scene captures` persist OCR, summaries, and risk observations
- `action cards` represent recommended next steps and operator follow-up
- `runs` track execution state, latency, audit events, and approvals
- `reasoning events` stream live progress to the Android app and browser deck

## Runtime flow

```text
Wearable / phone frame
  -> POST /api/scans/analyze
  -> saved under backend/data/uploads/
  -> durable session + run created
  -> agent tool loop
     -> run_ocr
     -> describe_scene
     -> search_documents
     -> make_decision
     -> save_scene_memory
  -> reasoning events and run state stored in SQLite
  -> SSE stream consumed by Android app
```

## Concurrency and latency notes

- Incoming chat and scan requests go through a bounded scheduler instead of
  unbounded `create_task` fan-out.
- Queue depth and active-run counts are exposed through `/api/system/metrics`.
- Document retrieval uses SQLite FTS when available, with a TTL cache for
  repeated queries.
- The agent warms document search early, then retries with OCR-enriched context
  only when the first retrieval misses.
- Tool calls are wrapped in a timeout guard to avoid one slow dependency
  stalling the whole interaction.

## Why Android Java

For this product, "frontend in Java" maps naturally to:

- phone companion app
- wearable bridge app
- camera/gallery input
- speech output through Android TTS

That keeps the field experience fast and practical for wearable and mobile
inspection workflows.
