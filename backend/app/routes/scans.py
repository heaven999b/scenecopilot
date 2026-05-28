from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..agent import core as agent_core
from ..agent import events as event_bus
from ..config import DEMO_USER_ID, UPLOADS_DIR
from ..domain.runtime_models import ArtifactType, RunStatus
from ..models import ChatResponse
from ..orchestration.planner import build_default_plan
from ..runtime import QueueFullError, scheduler
from ..runtime_profiles import AggregationPolicy, RuntimeProfile, get_runtime_profile, resolve_aggregation_policy
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from ..services.media_window_service import media_window_service
from ..services.session_manager import SessionHandle, session_manager
from ..services.window_aggregator_service import (
    BufferedFrame,
    ScanWindowState,
    window_aggregator_service,
)
from ..storage import copy_upload_to_path

router = APIRouter(prefix="/api/scans", tags=["scans"])
logger = logging.getLogger("scenecopilot.scans")


def _usable_transcript(text: str) -> bool:
    normalized = " ".join(text.split()).strip().lower()
    if not normalized:
        return False
    if normalized.startswith("no speech provider configured"):
        return False
    if normalized.startswith("audio clip received, but"):
        return False
    if normalized.startswith("audio clip missing or empty"):
        return False
    if "sidecar transcript" in normalized:
        return False
    if "local speech transcription is unavailable" in normalized:
        return False
    return True


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _frame_window_bounds(frames: list[BufferedFrame]) -> tuple[int | None, int | None]:
    timestamps = [frame.captured_at_ms for frame in frames if frame.captured_at_ms is not None]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def _frame_window_records(frames: list[BufferedFrame], *, primary_image_path: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for index, frame in enumerate(frames):
        records.append({
            "index": index,
            "image_name": Path(frame.image_path).name,
            "image_path": frame.image_path,
            "captured_at_ms": frame.captured_at_ms,
            "received_at_ms": int(frame.received_at * 1000),
            "size_bytes": frame.size_bytes,
            "visible_text_hint": (frame.visible_text or "").strip() or None,
            "retained_for_run": frame.image_path == primary_image_path,
        })
    return records


def _merged_visible_text(frames: list[BufferedFrame]) -> str | None:
    parts = _dedupe_preserve_order([frame.visible_text or "" for frame in frames])
    if not parts:
        return None
    return "\n".join(parts)


async def _cleanup_files(paths: list[str]) -> None:
    await asyncio.gather(
        *(asyncio.to_thread(Path(path).unlink, missing_ok=True) for path in paths),
        return_exceptions=True,
    )


async def _resolved_audio_context(
    *,
    session_id: str,
    runtime_profile: RuntimeProfile,
    captured_at_ms: int | None,
    frame_window_started_at_ms: int | None,
    frame_window_ended_at_ms: int | None,
) -> tuple[list[str], list[dict[str, object]], str, list[str], int | None, int | None]:
    anchor_start_ms = frame_window_started_at_ms if frame_window_started_at_ms is not None else captured_at_ms
    anchor_end_ms = frame_window_ended_at_ms if frame_window_ended_at_ms is not None else captured_at_ms
    interval_start_ms = (
        anchor_start_ms - runtime_profile.alignment_window_ms
        if anchor_start_ms is not None else None
    )
    interval_end_ms = (
        anchor_end_ms + runtime_profile.alignment_future_tolerance_ms
        if anchor_end_ms is not None else None
    )
    aligned_audio_windows = await asyncio.to_thread(
        media_window_service.list_audio_windows_for_interval,
        session_id,
        interval_start_ms=interval_start_ms,
        interval_end_ms=interval_end_ms,
        max_gap_ms=runtime_profile.alignment_window_ms,
        limit=runtime_profile.alignment_max_audio_windows,
    )
    aligned_audio_paths: list[str] = []
    aligned_audio_window_records: list[dict[str, object]] = []
    prefetched_transcripts: list[str] = []
    transcript_source_run_ids: list[str] = []
    for aligned_audio_window in aligned_audio_windows:
        aligned_audio_path = Path(aligned_audio_window["audio_path"]).resolve()
        if not aligned_audio_path.exists():
            continue
        aligned_audio_paths.append(str(aligned_audio_path))
        run_id = str(aligned_audio_window.get("run_id") or "").strip()
        transcript_reused = False
        transcript_artifact_id: int | None = None
        if run_id:
            transcript_artifact = await asyncio.to_thread(
                artifact_service.latest_artifact,
                run_id,
                ArtifactType.TRANSCRIPT,
            )
            if transcript_artifact is not None and transcript_artifact.get("provider") != "fallback":
                transcript_artifact_id = int(transcript_artifact["id"])
                transcript_content = transcript_artifact.get("content_json") or {}
                transcript_text = str(transcript_content.get("transcript") or "").strip()
                if _usable_transcript(transcript_text):
                    prefetched_transcripts.append(transcript_text)
                    transcript_source_run_ids.append(run_id)
                    transcript_reused = True
        aligned_audio_window_records.append({
            "audio_window_id": aligned_audio_window.get("id"),
            "upload_id": aligned_audio_window.get("upload_id"),
            "run_id": aligned_audio_window.get("run_id"),
            "started_at_ms": aligned_audio_window.get("started_at_ms"),
            "ended_at_ms": aligned_audio_window.get("ended_at_ms"),
            "capture_profile": aligned_audio_window.get("capture_profile"),
            "gap_ms": aligned_audio_window.get("gap_ms"),
            "overlap_ms": aligned_audio_window.get("overlap_ms"),
            "alignment_mode": aligned_audio_window.get("alignment_mode"),
            "transcript_reused": transcript_reused,
            "transcript_artifact_id": transcript_artifact_id,
        })
    transcript_source_run_ids = _dedupe_preserve_order(transcript_source_run_ids)
    prefetched_transcript = "\n".join(_dedupe_preserve_order(prefetched_transcripts))
    return (
        aligned_audio_paths,
        aligned_audio_window_records,
        prefetched_transcript,
        transcript_source_run_ids,
        interval_start_ms,
        interval_end_ms,
    )


async def _create_scan_run(
    *,
    prompt: str,
    session_id: str,
    runtime_profile: RuntimeProfile,
    aggregation_policy: AggregationPolicy,
    image_path: str,
    captured_at_ms: int | None,
    visible_text: str | None,
) -> SessionHandle:
    plan = build_default_plan(user_message=prompt, has_image=True, has_audio=False)
    handle = await asyncio.to_thread(
        session_manager.start_run,
        user_id=DEMO_USER_ID,
        user_message=prompt,
        session_id=session_id,
        trigger="scan",
        image_count=1,
        input_payload={
            "visible_text_hint": visible_text,
            "image_path": image_path,
            "captured_at_ms": captured_at_ms,
            "capture_profile": runtime_profile.profile_id,
            "aggregation_delay_ms": aggregation_policy.delay_ms,
            "aggregation_max_frames": aggregation_policy.max_frames,
            "aggregation_scene_gap_ms": aggregation_policy.scene_gap_ms,
            "aggregation_load_tier": aggregation_policy.load_tier,
            "frame_window_count": 1,
            "aggregation_state": "buffering",
        },
        plan=plan,
    )
    await asyncio.to_thread(
        session_manager.update_run_status,
        handle.run_id,
        status=RunStatus.QUEUED,
        current_stage="aggregating",
    )
    return handle


async def _flush_scan_window(window: ScanWindowState, flush_reason: str) -> int | None:
    frames = list(window.frames)
    primary_frame = frames[-1]
    supporting_paths = [frame.image_path for frame in frames[:-1]]
    merged_visible_text = _merged_visible_text(frames)
    frame_window_started_at_ms, frame_window_ended_at_ms = _frame_window_bounds(frames)
    frame_window_records = _frame_window_records(frames, primary_image_path=primary_frame.image_path)
    (
        aligned_audio_paths,
        aligned_audio_window_records,
        prefetched_transcript,
        transcript_source_run_ids,
        interval_start_ms,
        interval_end_ms,
    ) = await _resolved_audio_context(
        session_id=window.session_id,
        runtime_profile=window.runtime_profile,
        captured_at_ms=primary_frame.captured_at_ms,
        frame_window_started_at_ms=frame_window_started_at_ms,
        frame_window_ended_at_ms=frame_window_ended_at_ms,
    )
    plan = build_default_plan(
        user_message=window.prompt,
        has_image=True,
        has_audio=bool(aligned_audio_paths or prefetched_transcript),
    )
    await asyncio.to_thread(
        session_manager.merge_run_input,
        window.run_id,
        patch={
            "visible_text_hint": merged_visible_text,
            "image_path": primary_frame.image_path,
            "image_paths": [primary_frame.image_path],
            "captured_at_ms": primary_frame.captured_at_ms,
            "capture_profile": window.capture_profile,
            "aggregation_delay_ms": window.aggregation_delay_ms,
            "aggregation_max_frames": window.aggregation_max_frames,
            "aggregation_scene_gap_ms": window.aggregation_scene_gap_ms,
            "aggregation_load_tier": window.load_tier,
            "aggregation_state": "flushing",
            "flush_reason": flush_reason,
            "frame_window_count": len(frames),
            "frame_window_started_at_ms": frame_window_started_at_ms,
            "frame_window_ended_at_ms": frame_window_ended_at_ms,
            "frame_window": frame_window_records,
            "aligned_audio_windows": aligned_audio_window_records,
            "alignment_window_ms": window.runtime_profile.alignment_window_ms,
            "alignment_future_tolerance_ms": window.runtime_profile.alignment_future_tolerance_ms,
            "alignment_max_audio_windows": window.runtime_profile.alignment_max_audio_windows,
            "transcript_reused_count": len(transcript_source_run_ids),
        },
        image_count=1,
        plan=plan,
    )
    await asyncio.to_thread(
        session_manager.update_run_status,
        window.run_id,
        status=RunStatus.QUEUED,
        current_stage="aggregation_flush",
    )
    await asyncio.to_thread(
        artifact_service.record_artifact,
        session_id=window.session_id,
        run_id=window.run_id,
        artifact_type=ArtifactType.FRAME_WINDOW,
        stage="ingest",
        provider="scan_window_aggregator",
        content={
            "summary": (
                f"Buffered {len(frames)} adjacent frame(s) over "
                f"{max(0, (frame_window_ended_at_ms or 0) - (frame_window_started_at_ms or frame_window_ended_at_ms or 0))} ms "
                f"before launching a single scene run."
            ),
            "flush_reason": flush_reason,
            "capture_profile": window.capture_profile,
            "aggregation_delay_ms": window.aggregation_delay_ms,
            "aggregation_max_frames": window.aggregation_max_frames,
            "aggregation_scene_gap_ms": window.aggregation_scene_gap_ms,
            "aggregation_load_tier": window.load_tier,
            "frame_count": len(frames),
            "primary_image_path": primary_frame.image_path,
            "window_started_at_ms": frame_window_started_at_ms,
            "window_ended_at_ms": frame_window_ended_at_ms,
            "frames": frame_window_records,
        },
        user_id=DEMO_USER_ID,
    )
    await asyncio.to_thread(
        audit_service.record,
        session_id=window.session_id,
        run_id=window.run_id,
        event_type="scan_window_flushed",
        detail={
            "flush_reason": flush_reason,
            "capture_profile": window.capture_profile,
            "aggregation_delay_ms": window.aggregation_delay_ms,
            "aggregation_max_frames": window.aggregation_max_frames,
            "aggregation_scene_gap_ms": window.aggregation_scene_gap_ms,
            "aggregation_load_tier": window.load_tier,
            "frame_count": len(frames),
            "frame_window_started_at_ms": frame_window_started_at_ms,
            "frame_window_ended_at_ms": frame_window_ended_at_ms,
            "transcript_reused_count": len(transcript_source_run_ids),
        },
        user_id=DEMO_USER_ID,
    )
    await event_bus.emit_event(
        window.session_id,
        "stage",
        {
            "name": "aggregation_flush",
            "message": "Bundling nearby frames into one scene run before queue submission.",
            "flush_reason": flush_reason,
            "capture_profile": window.capture_profile,
            "frame_count": len(frames),
        },
        run_id=window.run_id,
        user_id=DEMO_USER_ID,
    )
    if aligned_audio_window_records:
        await asyncio.to_thread(
            artifact_service.record_artifact,
            session_id=window.session_id,
            run_id=window.run_id,
            artifact_type=ArtifactType.ALIGNMENT,
            stage="planner",
            provider="session_audio_multimodal_window",
            content={
                "summary": f"Aligned {len(aligned_audio_window_records)} recent audio window(s) to the buffered frame window.",
                "captured_at_ms": primary_frame.captured_at_ms,
                "capture_profile": window.capture_profile,
                "window_start_ms": interval_start_ms,
                "window_end_ms": interval_end_ms,
                "audio_window_count": len(aligned_audio_window_records),
                "audio_windows": aligned_audio_window_records,
                "alignment_max_audio_windows": window.runtime_profile.alignment_max_audio_windows,
                "transcript_reused_count": len(transcript_source_run_ids),
                "transcript_source_run_ids": transcript_source_run_ids,
            },
            user_id=DEMO_USER_ID,
        )
        await asyncio.to_thread(
            audit_service.record,
            session_id=window.session_id,
            run_id=window.run_id,
            event_type="multimodal_window_aligned",
            detail={
                "captured_at_ms": primary_frame.captured_at_ms,
                "capture_profile": window.capture_profile,
                "window_start_ms": interval_start_ms,
                "window_end_ms": interval_end_ms,
                "audio_window_count": len(aligned_audio_window_records),
                "alignment_max_audio_windows": window.runtime_profile.alignment_max_audio_windows,
                "transcript_reused_count": len(transcript_source_run_ids),
                "transcript_source_run_ids": transcript_source_run_ids,
            },
            user_id=DEMO_USER_ID,
        )
    if supporting_paths:
        await _cleanup_files(supporting_paths)
    try:
        return await scheduler.submit(
            lambda: agent_core.run_agent(
                user_message=window.prompt,
                session_id=window.session_id,
                image_paths=[primary_frame.image_path],
                audio_paths=aligned_audio_paths,
                prefetched_transcript=prefetched_transcript or None,
                transcript_source_run_id=transcript_source_run_ids[0] if transcript_source_run_ids else None,
                prefetched_transcript_source_run_ids=transcript_source_run_ids or None,
                visible_text=merged_visible_text,
                run_id=window.run_id,
                trigger="scan",
            ),
            session_id=window.session_id,
            run_id=window.run_id,
        )
    except QueueFullError as exc:
        await asyncio.to_thread(
            session_manager.update_run_status,
            window.run_id,
            status=RunStatus.CANCELLED,
            current_stage="queue_rejected",
            error_message="Run rejected because the queue is full.",
        )
        await _cleanup_files([primary_frame.image_path])
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="SceneCopilot is busy. Please retry in a moment.",
        ) from exc
    except Exception:
        await _cleanup_files([primary_frame.image_path])
        raise


@router.post("/analyze", response_model=ChatResponse)
async def analyze_scene(
    image: UploadFile = File(...),
    prompt: str = Form(default="Inspect this scene and tell me what I should do next."),
    session_id: str | None = Form(default=None),
    visible_text: str | None = Form(default=None),
    captured_at_ms: int | None = Form(default=None),
    capture_profile: str | None = Form(default=None),
) -> ChatResponse:
    if not image.filename:
        raise HTTPException(status_code=400, detail="Missing image filename")

    suffix = Path(image.filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="Only image uploads are supported here")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOADS_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
    await copy_upload_to_path(image, stored)
    effective_session_id = session_id or uuid.uuid4().hex[:12]
    runtime_profile = get_runtime_profile(capture_profile)
    scheduler_snapshot = await scheduler.snapshot()
    aggregation_policy = resolve_aggregation_policy(runtime_profile, scheduler_snapshot)

    try:
        result = await window_aggregator_service.register_frame(
            session_id=effective_session_id,
            prompt=prompt,
            capture_profile=runtime_profile.profile_id,
            runtime_profile=runtime_profile,
            aggregation_delay_ms=aggregation_policy.delay_ms,
            aggregation_max_frames=aggregation_policy.max_frames,
            aggregation_scene_gap_ms=aggregation_policy.scene_gap_ms,
            load_tier=aggregation_policy.load_tier,
            image_path=str(stored.resolve()),
            captured_at_ms=captured_at_ms,
            visible_text=visible_text,
            create_run=lambda: _create_scan_run(
                prompt=prompt,
                session_id=effective_session_id,
                runtime_profile=runtime_profile,
                aggregation_policy=aggregation_policy,
                image_path=str(stored.resolve()),
                captured_at_ms=captured_at_ms,
                visible_text=visible_text,
            ),
            on_flush=_flush_scan_window,
        )
    except HTTPException:
        await _cleanup_files([str(stored.resolve())])
        raise
    except Exception as exc:
        await _cleanup_files([str(stored.resolve())])
        logger.exception("failed to buffer scan frame")
        raise HTTPException(
            status_code=500,
            detail=f"Scene buffering failed: {type(exc).__name__}: {exc}",
        ) from exc

    if result.state == "failed":
        raise HTTPException(
            status_code=500,
            detail="Scene buffering failed before the run could be queued.",
        )
    if result.state == "aggregating":
        await asyncio.to_thread(
            session_manager.update_run_status,
            result.run_id,
            status=RunStatus.QUEUED,
            current_stage="aggregating",
        )
        await event_bus.emit_event(
            result.session_id,
            "stage",
            {
                "name": "aggregation",
                "message": (
                    "Buffering nearby frames before launching a single scene run."
                    if result.created_new_window
                    else "Merged an adjacent frame into the current scene window."
                ),
                "capture_profile": runtime_profile.profile_id,
                "frame_count": result.frame_count,
                "aggregation_delay_ms": aggregation_policy.delay_ms,
                "aggregation_max_frames": aggregation_policy.max_frames,
                "aggregation_scene_gap_ms": aggregation_policy.scene_gap_ms,
                "aggregation_load_tier": aggregation_policy.load_tier,
                "pending_runs": aggregation_policy.pending_runs,
                "active_runs": aggregation_policy.active_runs,
                "state": result.state,
            },
            run_id=result.run_id,
            user_id=DEMO_USER_ID,
        )
    elif result.state == "flushing":
        await event_bus.emit_event(
            result.session_id,
            "stage",
            {
                "name": "aggregation_flush",
                "message": "The buffered scene window is full and is being promoted to the run queue.",
                "capture_profile": runtime_profile.profile_id,
                "frame_count": result.frame_count,
                "aggregation_delay_ms": aggregation_policy.delay_ms,
                "aggregation_max_frames": aggregation_policy.max_frames,
                "aggregation_scene_gap_ms": aggregation_policy.scene_gap_ms,
                "aggregation_load_tier": aggregation_policy.load_tier,
                "pending_runs": aggregation_policy.pending_runs,
                "active_runs": aggregation_policy.active_runs,
                "state": result.state,
            },
            run_id=result.run_id,
            user_id=DEMO_USER_ID,
        )
    return ChatResponse(
        session_id=result.session_id,
        run_id=result.run_id,
        state=result.state,
        queue_position=result.queue_position,
    )
