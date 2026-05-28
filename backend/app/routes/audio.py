from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..agent import core as agent_core
from ..config import AUDIO_CHUNK_DIR, DEMO_USER_ID, UPLOADS_DIR
from ..domain.runtime_models import ArtifactType, RunStatus
from ..models import AudioChunkUploadResponse, ChatResponse
from ..orchestration.planner import build_default_plan
from ..runtime import QueueFullError, scheduler
from ..runtime_profiles import get_runtime_profile
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from ..services.media_window_service import media_window_service
from ..services.session_manager import session_manager
from ..storage import copy_upload_to_path

router = APIRouter(prefix="/api/audio", tags=["audio"])

_ALLOWED_AUDIO_SUFFIXES = {
    ".m4a",
    ".mp3",
    ".wav",
    ".webm",
    ".ogg",
    ".mp4",
    ".mpeg",
    ".mpga",
}

_ALLOWED_AUDIO_FORMATS = {
    "binary",
    "pcm16le_mono_16000",
}


def _validated_audio_ext(value: str) -> str:
    ext = value.strip().lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in _ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported audio format")
    return ext


def _validated_audio_format(value: str) -> str:
    audio_format = value.strip().lower()
    if audio_format not in _ALLOWED_AUDIO_FORMATS:
        raise HTTPException(status_code=400, detail="Unsupported audio format descriptor")
    return audio_format


def _assemble_chunked_audio(*, upload_id: str, audio_ext: str, final_chunk_index: int, audio_format: str) -> Path:
    chunk_dir = AUDIO_CHUNK_DIR / upload_id
    if not chunk_dir.exists():
        raise HTTPException(status_code=404, detail="Audio upload session not found")

    part_paths = sorted(chunk_dir.glob("*.part"))
    expected_count = final_chunk_index + 1
    if len(part_paths) != expected_count:
        raise HTTPException(status_code=409, detail="Audio chunks are incomplete")

    expected_names = [f"{idx:06d}.part" for idx in range(expected_count)]
    actual_names = [path.name for path in part_paths]
    if actual_names != expected_names:
        raise HTTPException(status_code=409, detail="Audio chunks are out of order or missing")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    assembled = UPLOADS_DIR / f"{upload_id}{audio_ext}"
    with assembled.open("wb") as output:
        if audio_format == "pcm16le_mono_16000":
            if audio_ext != ".wav":
                raise HTTPException(status_code=400, detail="pcm16le_mono_16000 chunks must assemble into .wav")
            data_size = sum(part.stat().st_size for part in part_paths)
            output.write(_wav_header(data_size, sample_rate=16000, channels=1, bits_per_sample=16))
        for part in part_paths:
            with part.open("rb") as handle:
                shutil.copyfileobj(handle, output, length=1024 * 1024)
    shutil.rmtree(chunk_dir, ignore_errors=True)
    return assembled


def _wav_header(data_size: int, *, sample_rate: int, channels: int, bits_per_sample: int) -> bytes:
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    riff_size = 36 + data_size
    return (
        b"RIFF"
        + riff_size.to_bytes(4, "little")
        + b"WAVE"
        + b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + channels.to_bytes(2, "little")
        + sample_rate.to_bytes(4, "little")
        + byte_rate.to_bytes(4, "little")
        + block_align.to_bytes(2, "little")
        + bits_per_sample.to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
    )


@router.post("/analyze", response_model=ChatResponse)
async def analyze_audio(
    audio: UploadFile = File(...),
    prompt: str = Form(default="Transcribe the spoken request and tell me the safest next step."),
    session_id: str | None = Form(default=None),
    window_started_at_ms: int | None = Form(default=None),
    window_ended_at_ms: int | None = Form(default=None),
    capture_profile: str | None = Form(default=None),
) -> ChatResponse:
    if not audio.filename:
        raise HTTPException(status_code=400, detail="Missing audio filename")

    suffix = Path(audio.filename).suffix.lower()
    if suffix not in _ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported audio format")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOADS_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
    await copy_upload_to_path(audio, stored)
    runtime_profile = get_runtime_profile(capture_profile)

    plan = build_default_plan(user_message=prompt, has_audio=True)
    handle = await asyncio.to_thread(
        session_manager.start_run,
        user_id=DEMO_USER_ID,
        user_message=prompt,
        session_id=session_id,
        trigger="audio",
        image_count=0,
        input_payload={
            "audio_path": str(stored.resolve()),
            "window_started_at_ms": window_started_at_ms,
            "window_ended_at_ms": window_ended_at_ms,
            "capture_profile": runtime_profile.profile_id,
        },
        plan=plan,
    )
    audio_window_id: int | None = None
    if window_started_at_ms is not None or window_ended_at_ms is not None:
        audio_window_id = await asyncio.to_thread(
            media_window_service.record_audio_window,
            session_id=handle.session_id,
            upload_id=stored.stem,
            audio_path=str(stored.resolve()),
            audio_format=suffix.lstrip("."),
            capture_profile=runtime_profile.profile_id,
            prompt=prompt,
            run_id=handle.run_id,
            started_at_ms=window_started_at_ms,
            ended_at_ms=window_ended_at_ms,
            user_id=DEMO_USER_ID,
        )
        await asyncio.to_thread(
            artifact_service.record_artifact,
            session_id=handle.session_id,
            run_id=handle.run_id,
            artifact_type=ArtifactType.ALIGNMENT,
            stage="ingest",
            provider="local_audio_window_store",
            content={
                "audio_window_id": audio_window_id,
                "upload_id": stored.stem,
                "started_at_ms": window_started_at_ms,
                "ended_at_ms": window_ended_at_ms,
                "audio_path": str(stored.resolve()),
                "capture_profile": runtime_profile.profile_id,
                "alignment_mode": "direct_audio",
            },
            user_id=DEMO_USER_ID,
        )
        await asyncio.to_thread(
            audit_service.record,
            session_id=handle.session_id,
            run_id=handle.run_id,
            event_type="audio_window_recorded",
            detail={
                "audio_window_id": audio_window_id,
                "started_at_ms": window_started_at_ms,
                "ended_at_ms": window_ended_at_ms,
                "audio_path": str(stored.resolve()),
                "capture_profile": runtime_profile.profile_id,
            },
            user_id=DEMO_USER_ID,
        )
    try:
        queue_position = await scheduler.submit(
            lambda: agent_core.run_agent(
                user_message=prompt,
                session_id=handle.session_id,
                audio_paths=[str(stored.resolve())],
                run_id=handle.run_id,
                trigger="audio",
            ),
            session_id=handle.session_id,
            run_id=handle.run_id,
        )
    except QueueFullError as exc:
        await asyncio.to_thread(
            session_manager.update_run_status,
            handle.run_id,
            status=RunStatus.CANCELLED,
            current_stage="queue_rejected",
            error_message="Run rejected because the queue is full.",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="SceneCopilot is busy. Please retry in a moment.",
        ) from exc
    return ChatResponse(
        session_id=handle.session_id,
        run_id=handle.run_id,
        state="queued",
        queue_position=queue_position,
    )


@router.post("/chunk", response_model=AudioChunkUploadResponse)
async def upload_audio_chunk(
    audio: UploadFile = File(...),
    prompt: str = Form(default="Transcribe the spoken request and tell me the safest next step."),
    session_id: str | None = Form(default=None),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    final_chunk: bool = Form(default=False),
    audio_ext: str = Form(default=".m4a"),
    audio_format: str = Form(default="binary"),
    window_started_at_ms: int | None = Form(default=None),
    window_ended_at_ms: int | None = Form(default=None),
    capture_profile: str | None = Form(default=None),
) -> AudioChunkUploadResponse:
    if chunk_index < 0:
        raise HTTPException(status_code=400, detail="chunk_index must be non-negative")
    if not upload_id.strip():
        raise HTTPException(status_code=400, detail="upload_id is required")

    validated_ext = _validated_audio_ext(audio_ext)
    validated_format = _validated_audio_format(audio_format)
    runtime_profile = get_runtime_profile(capture_profile)
    chunk_dir = AUDIO_CHUNK_DIR / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunk_dir / f"{chunk_index:06d}.part"
    await copy_upload_to_path(audio, chunk_path)

    effective_session_id = session_id or uuid.uuid4().hex[:12]
    if not final_chunk:
        return AudioChunkUploadResponse(
            upload_id=upload_id,
            session_id=effective_session_id,
            received_chunk=chunk_index,
            finalized=False,
            state="uploading",
        )

    stored = await asyncio.to_thread(
        _assemble_chunked_audio,
        upload_id=upload_id,
        audio_ext=validated_ext,
        final_chunk_index=chunk_index,
        audio_format=validated_format,
    )
    plan = build_default_plan(user_message=prompt, has_audio=True)
    handle = await asyncio.to_thread(
        session_manager.start_run,
        user_id=DEMO_USER_ID,
        user_message=prompt,
        session_id=effective_session_id,
        trigger="audio_chunked",
        image_count=0,
        input_payload={
            "audio_path": str(stored.resolve()),
            "upload_id": upload_id,
            "chunk_count": chunk_index + 1,
            "audio_format": validated_format,
            "window_started_at_ms": window_started_at_ms,
            "window_ended_at_ms": window_ended_at_ms,
            "capture_profile": runtime_profile.profile_id,
        },
        plan=plan,
    )
    audio_window_id = await asyncio.to_thread(
        media_window_service.record_audio_window,
        session_id=handle.session_id,
        upload_id=upload_id,
        audio_path=str(stored.resolve()),
        audio_format=validated_format,
        capture_profile=runtime_profile.profile_id,
        prompt=prompt,
        run_id=handle.run_id,
        started_at_ms=window_started_at_ms,
        ended_at_ms=window_ended_at_ms,
        user_id=DEMO_USER_ID,
    )
    await asyncio.to_thread(
        artifact_service.record_artifact,
        session_id=handle.session_id,
        run_id=handle.run_id,
        artifact_type=ArtifactType.ALIGNMENT,
        stage="ingest",
        provider="local_audio_window_store",
        content={
            "audio_window_id": audio_window_id,
            "upload_id": upload_id,
            "chunk_count": chunk_index + 1,
            "started_at_ms": window_started_at_ms,
            "ended_at_ms": window_ended_at_ms,
            "audio_format": validated_format,
            "audio_path": str(stored.resolve()),
            "capture_profile": runtime_profile.profile_id,
            "alignment_mode": "chunked_audio",
        },
        user_id=DEMO_USER_ID,
    )
    await asyncio.to_thread(
        audit_service.record,
        session_id=handle.session_id,
        run_id=handle.run_id,
        event_type="audio_window_recorded",
        detail={
            "audio_window_id": audio_window_id,
            "upload_id": upload_id,
            "started_at_ms": window_started_at_ms,
            "ended_at_ms": window_ended_at_ms,
            "audio_format": validated_format,
            "capture_profile": runtime_profile.profile_id,
        },
        user_id=DEMO_USER_ID,
    )
    try:
        queue_position = await scheduler.submit(
            lambda: agent_core.run_agent(
                user_message=prompt,
                session_id=handle.session_id,
                audio_paths=[str(stored.resolve())],
                run_id=handle.run_id,
                trigger="audio_chunked",
            ),
            session_id=handle.session_id,
            run_id=handle.run_id,
        )
    except QueueFullError as exc:
        await asyncio.to_thread(
            session_manager.update_run_status,
            handle.run_id,
            status=RunStatus.CANCELLED,
            current_stage="queue_rejected",
            error_message="Run rejected because the queue is full.",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="SceneCopilot is busy. Please retry in a moment.",
        ) from exc

    return AudioChunkUploadResponse(
        upload_id=upload_id,
        session_id=handle.session_id,
        received_chunk=chunk_index,
        finalized=True,
        state="queued",
        run_id=handle.run_id,
        queue_position=queue_position,
    )
