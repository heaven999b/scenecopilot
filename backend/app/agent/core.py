"""Structured run execution for SceneCopilot."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import time
from typing import Any

from ..config import DEMO_USER_ID
from ..db import conn_ctx, get_conn
from ..domain.runtime_models import (
    ActionRecommendation,
    ArtifactType,
    FrameRef,
    InterventionType,
    RetrievalHit,
    RiskLevel,
    RunStatus,
    SceneObservation,
)
from ..observability.metrics import Timer
from ..orchestration.planner import build_default_plan
from ..orchestration.policies import (
    choose_latency_tier,
    choose_intervention_policy,
    choose_ocr_policy,
    choose_retrieval_policy,
    classify_risk_taxonomy,
    evaluate_clarification_policy,
)
from ..providers.registry import provider_bundle
from ..runtime import scheduler
from ..runtime_profiles import resolve_execution_pressure
from ..services.artifact_service import artifact_service
from ..services.audit_service import audit_service
from ..services.choice_manager_service import choice_manager_service
from ..services.grounding_service import grounding_service
from ..services.pipeline_service import scene_pipeline_service
from ..services.scene_memory_service import scene_memory_service
from ..services.session_manager import session_manager
from . import events as event_bus


def _persist_chat(
    user_id: int,
    role: str,
    content: str,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    with conn_ctx() as conn:
        conn.execute(
            """
            INSERT INTO chat_messages (user_id, session_id, run_id, role, content, tool_calls_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, session_id, run_id, role, content, json.dumps(tool_calls or [], default=str)),
        )


def _load_recent_context(session_id: str | None, user_id: int) -> list[str]:
    if not session_id:
        return []
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT role, content
            FROM chat_messages
            WHERE session_id = ? AND user_id = ?
            ORDER BY id DESC
            LIMIT 6
            """,
            (session_id, user_id),
        ).fetchall()
    finally:
        conn.close()
    return [f"{row['role']}: {row['content']}" for row in reversed(rows) if row["content"]]


def _guess_mime_type(path: str) -> str:
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or "application/octet-stream"


def _best_doc_query(user_message: str, transcript: str, ocr_text: str, scene_summary: str) -> str:
    seeds = [user_message.strip(), transcript.strip(), ocr_text.strip(), scene_summary.strip()]
    query = " ".join(part for part in seeds if part)
    return query[:320]


def _existing_paths(values: list[str]) -> list[str]:
    filtered: list[str] = []
    for value in values:
        path = str(value or "").strip()
        if not path:
            continue
        if path not in filtered:
            filtered.append(path)
    return filtered


def _merge_transcripts(parts: list[str]) -> str:
    seen: set[str] = set()
    merged: list[str] = []
    for part in parts:
        normalized = " ".join(part.split()).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return "\n".join(merged)


def _merge_uncertainty_levels(first: str, second: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    first_score = order.get(first, 0)
    second_score = order.get(second, 0)
    return first if first_score >= second_score else second


def _option_labels(decision: ActionRecommendation) -> str:
    if not decision.choice_card or not decision.choice_card.options:
        return ""
    return ", ".join(option.label for option in decision.choice_card.options[:4])


def _default_scene_observation(summary: str) -> SceneObservation:
    return SceneObservation(
        summary=summary,
        risk_level=RiskLevel.LOW,
        provider="runtime_default",
    )


def _build_continuation_context(parent_run: dict[str, Any] | None, parent_decision: dict[str, Any] | None, continuation_reason: str | None) -> str:
    if not parent_run:
        return ""
    parts = [
        f"Continuation reason: {continuation_reason or 'followup'}",
        f"Parent request: {parent_run.get('user_message') or ''}",
    ]
    output_text = str(parent_run.get("output_text") or "").strip()
    if output_text:
        parts.append(f"Parent output: {output_text[:280]}")
    if parent_decision:
        title = str(parent_decision.get("title") or "").strip()
        recommendation = str(parent_decision.get("recommendation") or "").strip()
        clarification_question = str(parent_decision.get("clarification_question") or "").strip()
        supporting_docs = ", ".join(parent_decision.get("supporting_doc_titles") or [])
        if title:
            parts.append(f"Parent decision title: {title}")
        if recommendation:
            parts.append(f"Parent recommendation: {recommendation[:240]}")
        if clarification_question:
            parts.append(f"Outstanding clarification: {clarification_question}")
        if supporting_docs:
            parts.append(f"Previously relevant docs: {supporting_docs}")
    return " | ".join(part for part in parts if part)


def _build_approval_resume_context(approved_action_plan: dict[str, Any] | None) -> str:
    if not approved_action_plan:
        return ""
    parts = ["This run resumes an already approved action plan."]
    title = str(approved_action_plan.get("approved_title") or "").strip()
    recommendation = str(approved_action_plan.get("approved_recommendation") or "").strip()
    reviewer_note = str(approved_action_plan.get("reviewer_note") or "").strip()
    if title:
        parts.append(f"Approved title: {title}")
    if recommendation:
        parts.append(f"Approved recommendation: {recommendation[:240]}")
    steps = [str(item).strip() for item in approved_action_plan.get("approved_next_steps", []) if str(item).strip()]
    if steps:
        parts.append("Approved steps: " + " | ".join(steps[:4]))
    supporting_docs = ", ".join(str(item).strip() for item in approved_action_plan.get("supporting_doc_titles", []) if str(item).strip())
    if supporting_docs:
        parts.append(f"Approved docs: {supporting_docs}")
    if reviewer_note:
        parts.append(f"Reviewer note: {reviewer_note}")
    return " | ".join(parts)


def _apply_approval_resume_bias(
    *,
    continuation_reason: str | None,
    approved_action_plan: dict[str, Any] | None,
    recommendation: ActionRecommendation,
    clarification,
    intervention,
) -> tuple[ActionRecommendation, Any, Any]:
    if continuation_reason != "approval_resume" or not approved_action_plan:
        return recommendation, clarification, intervention

    approved_steps = [
        str(item).strip()
        for item in approved_action_plan.get("approved_next_steps", [])
        if str(item).strip()
    ]
    approved_docs = [
        str(item).strip()
        for item in approved_action_plan.get("supporting_doc_titles", [])
        if str(item).strip()
    ]
    approved_recommendation = str(approved_action_plan.get("approved_recommendation") or "").strip()
    approved_title = str(approved_action_plan.get("approved_title") or "").strip()

    merged_steps: list[str] = []
    for item in approved_steps + recommendation.next_steps:
        if item and item not in merged_steps:
            merged_steps.append(item)
    if merged_steps:
        recommendation.next_steps = merged_steps[:6]
    if approved_docs:
        merged_docs: list[str] = []
        for item in approved_docs + recommendation.supporting_doc_titles:
            if item and item not in merged_docs:
                merged_docs.append(item)
        recommendation.supporting_doc_titles = merged_docs[:6]
    if approved_title and recommendation.title:
        recommendation.title = f"{approved_title} · resume"
    elif approved_title:
        recommendation.title = approved_title
    if approved_recommendation and approved_recommendation not in recommendation.recommendation:
        recommendation.recommendation = f"{approved_recommendation} Current confirmation: {recommendation.recommendation}"

    recommendation.approval_required = False
    recommendation.blocked = False
    if clarification.required and recommendation.uncertainty_level != "high" and approved_steps:
        clarification.required = False
        clarification.reason = "A previously approved action plan exists for this continuation."
        clarification.question = None
        recommendation.clarification_question = None
        recommendation.evidence_supported = True
    if not clarification.required:
        recommendation.intervention_type = InterventionType.RECOMMEND_ACTION
        intervention.intervention_type = InterventionType.RECOMMEND_ACTION
        intervention.show_choice_card = True
        intervention.reason = "This run resumes an already approved action path and should continue instead of re-requesting approval."
    return recommendation, clarification, intervention


def _compose_final(
    *,
    transcript: str,
    ocr_text: str,
    scene_summary: str,
    documents: list[dict[str, Any]],
    decision: ActionRecommendation,
) -> str:
    parts = [decision.recommendation.strip()]
    if decision.intervention_type == InterventionType.ASK_CLARIFICATION and decision.clarification_question:
        parts = [decision.clarification_question.strip()]
    recommendation_lower = decision.recommendation.lower()
    if decision.approval_required and "approval is required" not in recommendation_lower and "review is required" not in recommendation_lower:
        parts.append("Human review is required before proceeding.")
    if not decision.evidence_supported:
        parts.append("The current guidance is based on incomplete evidence, so a clearer view or manual lookup is recommended.")
    if transcript:
        parts.append(f"Audio context: {transcript[:180].strip()}")
    if ocr_text:
        parts.append(f"Visible text: {ocr_text[:240].strip()}")
    parts.append(f"Scene summary: {scene_summary}")
    if documents:
        doc_titles = ", ".join(item["title"] for item in documents[:3])
        parts.append(f"Relevant docs: {doc_titles}")
    if decision.next_steps:
        steps = " ".join(f"{idx + 1}. {step}" for idx, step in enumerate(decision.next_steps))
        parts.append(f"Next steps: {steps}")
    option_labels = _option_labels(decision)
    if option_labels:
        parts.append(f"Options: {option_labels}")
    return " ".join(part for part in parts if part)


async def _transition_run(
    run_id: str,
    *,
    status: RunStatus,
    current_stage: str | None = None,
    route_name: str | None = None,
    output_text: str | None = None,
    latency_ms: float | None = None,
    error_message: str | None = None,
) -> None:
    await asyncio.to_thread(
        session_manager.update_run_status,
        run_id,
        status=status,
        current_stage=current_stage,
        route_name=route_name,
        output_text=output_text,
        latency_ms=latency_ms,
        error_message=error_message,
    )


async def _emit_stage(
    *,
    session_id: str,
    run_id: str,
    status: RunStatus,
    name: str,
    message: str,
    user_id: int,
    extra: dict[str, Any] | None = None,
) -> None:
    await _transition_run(run_id, status=status, current_stage=name)
    payload = {"name": name, "message": message}
    if extra:
        payload.update(extra)
    await event_bus.emit_event(
        session_id,
        "stage",
        payload,
        run_id=run_id,
        user_id=user_id,
    )


async def _record_stage_timing(
    *,
    run_id: str,
    stage_name: str,
    duration_ms: float,
    extra: dict[str, Any] | None = None,
) -> None:
    await asyncio.to_thread(
        session_manager.merge_run_timing,
        run_id,
        stage_name=stage_name,
        duration_ms=duration_ms,
        extra=extra,
    )


async def _record_audit(
    *,
    session_id: str,
    run_id: str,
    event_type: str,
    detail: dict[str, Any],
    user_id: int,
) -> None:
    await asyncio.to_thread(
        audit_service.record,
        session_id=session_id,
        run_id=run_id,
        event_type=event_type,
        detail=detail,
        user_id=user_id,
    )


async def _emit_artifact(
    *,
    session_id: str,
    run_id: str,
    artifact_type: str,
    payload: dict[str, Any],
    user_id: int,
) -> None:
    await event_bus.emit_event(
        session_id,
        "artifact",
        {"artifact_type": artifact_type, **payload},
        run_id=run_id,
        user_id=user_id,
    )


async def run_agent(
    user_message: str,
    session_id: str | None = None,
    image_paths: list[str] | None = None,
    audio_paths: list[str] | None = None,
    *,
    prefetched_transcript: str | None = None,
    transcript_source_run_id: str | None = None,
    prefetched_transcript_source_run_ids: list[str] | None = None,
    visible_text: str | None = None,
    run_id: str | None = None,
    trigger: str = "chat",
    user_id: int = DEMO_USER_ID,
) -> dict[str, Any]:
    run_started = time.perf_counter()
    image_paths = image_paths or []
    audio_paths = audio_paths or []
    plan = build_default_plan(
        user_message=user_message,
        has_image=bool(image_paths),
        has_audio=bool(audio_paths or prefetched_transcript),
    )

    if run_id is None:
        handle = await asyncio.to_thread(
            session_manager.start_run,
            user_id=user_id,
            user_message=user_message,
            session_id=session_id,
            trigger=trigger,
            image_count=len(image_paths),
            input_payload={
                "visible_text_hint": visible_text,
                "image_paths": image_paths,
                "audio_paths": audio_paths,
                "prefetched_transcript": prefetched_transcript,
                "transcript_source_run_id": transcript_source_run_id,
                "prefetched_transcript_source_run_ids": prefetched_transcript_source_run_ids,
            },
            plan=plan,
        )
        session_id = handle.session_id
        run_id = handle.run_id

    if session_id is None:
        raise ValueError("session_id is required when run_id is provided")

    run_record: dict[str, Any] | None = None
    stored_input: dict[str, Any] = {}
    if run_id is not None:
        run_record = await asyncio.to_thread(session_manager.get_run, run_id)
        if run_record is not None:
            stored_input = dict(run_record.get("input_json") or {})
            image_paths = _existing_paths(list(image_paths or []) or list(stored_input.get("image_paths") or []))
            audio_paths = _existing_paths(list(audio_paths or []) or list(stored_input.get("audio_paths") or []))
            visible_text = visible_text if visible_text is not None else stored_input.get("visible_text_hint")
            if not prefetched_transcript:
                prefetched_transcript = stored_input.get("prefetched_transcript")
            if transcript_source_run_id is None:
                transcript_source_run_id = stored_input.get("transcript_source_run_id")
            if not prefetched_transcript_source_run_ids:
                prefetched_transcript_source_run_ids = stored_input.get("prefetched_transcript_source_run_ids")
            if not image_paths:
                maybe_single = str(stored_input.get("image_path") or "").strip()
                if maybe_single:
                    image_paths = [maybe_single]
            if not audio_paths:
                maybe_single_audio = str(stored_input.get("audio_path") or "").strip()
                if maybe_single_audio:
                    audio_paths = [maybe_single_audio]

    transcript = (prefetched_transcript or "").strip()
    ocr_text = ""
    scene_summary = "No scene image was provided, so the answer is based on the user request and matching documents only."
    scene_risk = RiskLevel.LOW
    scene_result = _default_scene_observation(scene_summary)
    document_hits: list[dict[str, Any]] = []
    memory_context_text = ""
    parent_run_id = str(stored_input.get("parent_run_id") or "").strip() or None
    continuation_reason = str(stored_input.get("continuation_reason") or "").strip() or None
    parent_decision = stored_input.get("parent_decision") if isinstance(stored_input.get("parent_decision"), dict) else None
    approved_action_plan = stored_input.get("approved_action_plan") if isinstance(stored_input.get("approved_action_plan"), dict) else None

    try:
        await asyncio.to_thread(
            _persist_chat,
            user_id,
            "user",
            user_message,
            session_id=session_id,
            run_id=run_id,
        )

        await _emit_stage(
            session_id=session_id,
            run_id=run_id,
            status=RunStatus.CAPTURING_CONTEXT,
            name="planner",
            message="Building the execution plan and loading recent session context.",
            user_id=user_id,
        )
        latency_tier = choose_latency_tier(user_message, has_image=bool(image_paths))
        scheduler_snapshot = await scheduler.snapshot()
        pressure_policy = resolve_execution_pressure(
            scheduler_snapshot,
            latency_tier=latency_tier,
        )
        context = await asyncio.to_thread(_load_recent_context, session_id, user_id)
        memory_context_text = await asyncio.to_thread(
            scene_memory_service.summarize_session_memory,
            session_id,
            limit=pressure_policy.memory_capture_limit,
        )
        if parent_run_id:
            parent_run = await asyncio.to_thread(session_manager.get_run, parent_run_id)
            continuation_context = _build_continuation_context(parent_run, parent_decision, continuation_reason)
            approval_resume_context = _build_approval_resume_context(approved_action_plan)
            memory_context_text = " | ".join(
                part
                for part in (memory_context_text, continuation_context, approval_resume_context)
                if part
            )
        await event_bus.emit_event(
            session_id,
            "run_plan",
            {
                "route_name": plan.route_name,
                "modalities": [item.value for item in plan.modalities],
                "steps": [
                    {
                        "step_type": step.step_type.value,
                        "required": step.required,
                        "rationale": step.rationale,
                    }
                    for step in plan.steps
                ],
                "context_turns": len(context),
                "latency_tier": latency_tier,
                "pressure_policy": pressure_policy.as_dict(),
                "memory_context_available": bool(memory_context_text),
                "parent_run_id": parent_run_id,
                "continuation_reason": continuation_reason,
                "approval_resume_active": bool(approved_action_plan),
            },
            run_id=run_id,
            user_id=user_id,
        )
        await _record_audit(
            session_id=session_id,
            run_id=run_id,
            event_type="execution_plan_selected",
            detail={
                "route_name": plan.route_name,
                "modalities": [item.value for item in plan.modalities],
                "latency_tier": latency_tier,
                "context_turns": len(context),
                "pressure_policy": pressure_policy.as_dict(),
                "memory_context_available": bool(memory_context_text),
                "parent_run_id": parent_run_id,
                "continuation_reason": continuation_reason,
                "approval_resume_active": bool(approved_action_plan),
            },
            user_id=user_id,
        )
        await event_bus.emit_event(
            session_id,
            "pressure_policy",
            pressure_policy.as_dict(),
            run_id=run_id,
            user_id=user_id,
        )

        combined_prompt = user_message
        if transcript:
            await _emit_stage(
                session_id=session_id,
                run_id=run_id,
                status=RunStatus.RUNNING_ASR,
                name="asr_reuse",
                message="Reusing transcript from a recent aligned audio window.",
                user_id=user_id,
            )
            await asyncio.to_thread(
                artifact_service.record_artifact,
                session_id=session_id,
                run_id=run_id,
                artifact_type=ArtifactType.TRANSCRIPT,
                stage="asr_reuse",
                provider="aligned_audio_window",
                content={
                    "transcript": transcript,
                    "source_run_id": transcript_source_run_id,
                    "source_run_ids": prefetched_transcript_source_run_ids or (
                        [transcript_source_run_id] if transcript_source_run_id else []
                    ),
                    "reused": True,
                },
                user_id=user_id,
            )
            await _record_audit(
                session_id=session_id,
                run_id=run_id,
                event_type="transcript_reused",
                detail={
                    "source_run_id": transcript_source_run_id,
                    "source_run_ids": prefetched_transcript_source_run_ids or (
                        [transcript_source_run_id] if transcript_source_run_id else []
                    ),
                    "preview": transcript[:180],
                },
                user_id=user_id,
            )
            await _emit_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type="transcript",
                payload={
                    "preview": transcript[:180],
                    "reused": True,
                    "source_run_id": transcript_source_run_id,
                    "source_run_ids": prefetched_transcript_source_run_ids or (
                        [transcript_source_run_id] if transcript_source_run_id else []
                    ),
                },
                user_id=user_id,
            )
            combined_prompt = " ".join(part for part in (user_message, transcript) if part.strip())
        elif audio_paths:
            await _emit_stage(
                session_id=session_id,
                run_id=run_id,
                status=RunStatus.RUNNING_ASR,
                name="asr",
                message="Transcribing audio context from the wearable or companion device.",
                user_id=user_id,
            )
            with Timer("asr") as timer:
                transcript_parts: list[str] = []
                for audio_path in audio_paths:
                    transcript_parts.append(
                        await scene_pipeline_service.run_asr(
                            session_id=session_id,
                            run_id=run_id,
                            audio_path=audio_path,
                            providers=provider_bundle.speech,
                            user_id=user_id,
                        )
                    )
                transcript = _merge_transcripts(transcript_parts)
            asr_latency_ms = timer.sample().duration_ms
            await _record_stage_timing(
                run_id=run_id,
                stage_name="asr",
                duration_ms=asr_latency_ms,
                extra={"audio_count": len(audio_paths)},
            )
            await _emit_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type="transcript",
                payload={
                    "preview": transcript[:180],
                    "latency_ms": asr_latency_ms,
                    "audio_count": len(audio_paths),
                },
                user_id=user_id,
            )
            combined_prompt = " ".join(part for part in (user_message, transcript) if part.strip())

        ocr_policy = choose_ocr_policy(combined_prompt, bool(image_paths))
        if pressure_policy.prefer_fast_ocr and image_paths:
            ocr_policy = type(ocr_policy)(
                fast_path=True,
                reason=f"{ocr_policy.reason} Runtime pressure is {pressure_policy.load_tier}, so OCR is forced onto the fast path.",
            )
        initial_retrieval_policy = choose_retrieval_policy(combined_prompt, bool(image_paths))
        if not pressure_policy.allow_optional_retrieval and "optional" in initial_retrieval_policy.reason.lower():
            initial_retrieval_policy = type(initial_retrieval_policy)(
                required=False,
                reason="Retrieval was deferred because the runtime is congested and the request is not explicitly grounding-critical.",
            )
        await _record_audit(
            session_id=session_id,
            run_id=run_id,
            event_type="policy_selected",
            detail={
                "ocr_fast_path": ocr_policy.fast_path,
                "ocr_reason": ocr_policy.reason,
                "retrieval_required": initial_retrieval_policy.required,
                "retrieval_reason": initial_retrieval_policy.reason,
                "pressure_load_tier": pressure_policy.load_tier,
                "retrieval_limit": pressure_policy.retrieval_limit,
            },
            user_id=user_id,
        )
        await event_bus.emit_event(
            session_id,
            "policy",
            {
                "ocr_fast_path": ocr_policy.fast_path,
                "ocr_reason": ocr_policy.reason,
                "retrieval_required": initial_retrieval_policy.required,
                "retrieval_reason": initial_retrieval_policy.reason,
                "pressure_load_tier": pressure_policy.load_tier,
                "retrieval_limit": pressure_policy.retrieval_limit,
            },
            run_id=run_id,
            user_id=user_id,
        )

        warm_retrieval_task: asyncio.Task[list[Any]] | None = None
        warm_embedding_task: asyncio.Task[list[float]] | None = None
        if initial_retrieval_policy.required and not ocr_policy.fast_path and pressure_policy.warm_retrieval:
            query = combined_prompt[:280]
            warm_embedding_task = asyncio.create_task(
                scene_pipeline_service.run_embedding(
                    session_id=session_id,
                    run_id=run_id,
                    text=query,
                    providers=provider_bundle.embedding,
                    user_id=user_id,
                )
            )
            if not ocr_policy.fast_path:
                warm_retrieval_task = asyncio.create_task(
                    scene_pipeline_service.run_retrieval(
                        session_id=session_id,
                        run_id=run_id,
                        query=query,
                        providers=provider_bundle.retrieval,
                        limit=pressure_policy.retrieval_limit,
                        user_id=user_id,
                    )
                )

        if image_paths:
            frame = FrameRef(
                artifact_id=f"{run_id}:frame:0",
                uri=image_paths[0],
                mime_type=_guess_mime_type(image_paths[0]),
            )
            await _emit_stage(
                session_id=session_id,
                run_id=run_id,
                status=RunStatus.RUNNING_OCR,
                name="ocr",
                message="Reading visible text from the current frame.",
                user_id=user_id,
                extra={"fast_path": ocr_policy.fast_path},
            )
            with Timer("ocr") as timer:
                ocr_result = await scene_pipeline_service.run_ocr(
                    session_id=session_id,
                    run_id=run_id,
                    frame=frame,
                    visible_text_hint=visible_text,
                    providers=provider_bundle.ocr,
                    user_id=user_id,
                )
            ocr_text = ocr_result.text
            ocr_latency_ms = timer.sample().duration_ms
            await _record_stage_timing(
                run_id=run_id,
                stage_name="ocr",
                duration_ms=ocr_latency_ms,
                extra={"provider": ocr_result.provider},
            )
            await _emit_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type="ocr",
                payload={
                    "provider": ocr_result.provider,
                    "preview": ocr_text[:180],
                    "latency_ms": ocr_latency_ms,
                },
                user_id=user_id,
            )

            await _emit_stage(
                session_id=session_id,
                run_id=run_id,
                status=RunStatus.RUNNING_VISION,
                name="vision",
                message="Interpreting the scene and estimating risk.",
                user_id=user_id,
            )
            with Timer("vision") as timer:
                scene_result = await scene_pipeline_service.run_vision(
                    session_id=session_id,
                    run_id=run_id,
                    frame=frame,
                    prompt=combined_prompt,
                    ocr_text=ocr_text,
                    providers=provider_bundle.vision,
                    user_id=user_id,
                )
            scene_summary = scene_result.summary
            scene_risk = scene_result.risk_level
            vision_latency_ms = timer.sample().duration_ms
            await _record_stage_timing(
                run_id=run_id,
                stage_name="vision",
                duration_ms=vision_latency_ms,
                extra={"provider": scene_result.provider, "risk_level": scene_risk.value},
            )
            await _emit_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type="scene_observation",
                payload={
                    "provider": scene_result.provider,
                    "summary": scene_summary,
                    "risk_level": scene_risk.value,
                    "tags": scene_result.tags,
                    "uncertainty_level": scene_result.uncertainty_level,
                    "layout_summary": scene_result.structure.layout_summary,
                    "hazard_cues": [item.label for item in scene_result.structure.hazard_cues[:4]],
                    "latency_ms": vision_latency_ms,
                },
                user_id=user_id,
            )

        refined_retrieval_policy = choose_retrieval_policy(
            combined_prompt,
            bool(image_paths),
            risk_hint=scene_risk.value if image_paths else None,
        )
        if not pressure_policy.allow_optional_retrieval and "optional" in refined_retrieval_policy.reason.lower():
            refined_retrieval_policy = type(refined_retrieval_policy)(
                required=False,
                reason="Retrieval was deferred because the runtime is congested and the request is not explicitly grounding-critical.",
            )
        await _record_audit(
            session_id=session_id,
            run_id=run_id,
            event_type="retrieval_policy_recomputed",
            detail={
                "required": refined_retrieval_policy.required,
                "reason": refined_retrieval_policy.reason,
                "risk_hint": scene_risk.value if image_paths else None,
                "pressure_load_tier": pressure_policy.load_tier,
            },
            user_id=user_id,
        )

        if warm_embedding_task is not None:
            await warm_embedding_task

        if refined_retrieval_policy.required:
            refined_query = _best_doc_query(user_message, transcript, ocr_text, scene_summary)
            await _emit_stage(
                session_id=session_id,
                run_id=run_id,
                status=RunStatus.RUNNING_RETRIEVAL,
                name="retrieval",
                message="Searching manuals, SOPs, and uploaded documents.",
                user_id=user_id,
            )
            with Timer("retrieval") as timer:
                if warm_retrieval_task is not None:
                    hits = await warm_retrieval_task
                else:
                    embedding_task = asyncio.create_task(
                        scene_pipeline_service.run_embedding(
                            session_id=session_id,
                            run_id=run_id,
                            text=refined_query,
                            providers=provider_bundle.embedding,
                            user_id=user_id,
                        )
                    )
                    hits = await scene_pipeline_service.run_retrieval(
                        session_id=session_id,
                        run_id=run_id,
                        query=refined_query,
                        providers=provider_bundle.retrieval,
                        limit=pressure_policy.retrieval_limit,
                        user_id=user_id,
                    )
                    await embedding_task
            retrieval_latency_ms = timer.sample().duration_ms
            await _record_stage_timing(
                run_id=run_id,
                stage_name="retrieval",
                duration_ms=retrieval_latency_ms,
                extra={"hit_count": len(hits)},
            )
            if image_paths and refined_query and not hits and refined_query != combined_prompt[:280]:
                await event_bus.emit_event(
                    session_id,
                    "stage",
                    {
                        "name": "retrieval_retry",
                        "message": "Retrying retrieval with OCR-enriched scene context.",
                    },
                    run_id=run_id,
                    user_id=user_id,
                )
                hits = await scene_pipeline_service.run_retrieval(
                    session_id=session_id,
                    run_id=run_id,
                    query=refined_query,
                    providers=provider_bundle.retrieval,
                    limit=pressure_policy.retrieval_limit,
                    user_id=user_id,
                )
            document_hits = [
                {
                    "id": hit.document_id,
                    "title": hit.title,
                    "snippet": hit.snippet,
                    "score": hit.score,
                    "source": hit.source,
                }
                for hit in hits
            ]
            await _emit_artifact(
                session_id=session_id,
                run_id=run_id,
                artifact_type="retrieval_hits",
                payload={
                    "query": refined_query,
                    "hit_count": len(document_hits),
                    "titles": [item["title"] for item in document_hits[:3]],
                    "latency_ms": retrieval_latency_ms,
                },
                user_id=user_id,
            )

        await _emit_stage(
            session_id=session_id,
            run_id=run_id,
            status=RunStatus.SYNTHESIZING,
            name="decision",
            message="Combining artifacts into an explicit recommendation.",
            user_id=user_id,
        )
        with Timer("decision") as timer:
            recommendation = await scene_pipeline_service.run_decision(
                session_id=session_id,
                run_id=run_id,
                prompt=combined_prompt,
                scene_summary=scene_summary,
                ocr_text=ocr_text,
                scene_structure=scene_result.structure,
                memory_context=memory_context_text,
                retrieved_docs=[
                    RetrievalHit(
                        document_id=item["id"],
                        title=item["title"],
                        snippet=item["snippet"],
                        score=float(item["score"]),
                        source=item["source"],
                    )
                    for item in document_hits
                ],
                providers=provider_bundle.decision,
                user_id=user_id,
            )
        decision_latency_ms = timer.sample().duration_ms
        await _record_stage_timing(
            run_id=run_id,
            stage_name="decision",
            duration_ms=decision_latency_ms,
            extra={"doc_count": len(document_hits)},
        )
        recommendation.uncertainty_level = _merge_uncertainty_levels(
            recommendation.uncertainty_level,
            scene_result.uncertainty_level,
        )
        if not recommendation.supporting_doc_titles:
            recommendation.supporting_doc_titles = [item["title"] for item in document_hits[:3]]
        clarification = evaluate_clarification_policy(
            user_message=user_message,
            ocr_text=ocr_text,
            scene_observation=scene_result,
            retrieved_document_count=len(document_hits),
        )
        risk_taxonomy = classify_risk_taxonomy(
            user_message=user_message,
            scene_observation=scene_result,
            recommendation=recommendation,
            retrieved_document_count=len(document_hits),
        )
        intervention = choose_intervention_policy(
            recommendation=recommendation,
            clarification=clarification,
            risk_taxonomy=risk_taxonomy,
        )
        recommendation, clarification, intervention = _apply_approval_resume_bias(
            continuation_reason=continuation_reason,
            approved_action_plan=approved_action_plan,
            recommendation=recommendation,
            clarification=clarification,
            intervention=intervention,
        )
        recommendation.intervention_type = intervention.intervention_type
        recommendation.risk_level = risk_taxonomy.risk_level
        if clarification.required:
            recommendation.title = "Need a clearer view before proceeding"
            recommendation.clarification_question = clarification.question
            recommendation.recommendation = clarification.question or recommendation.recommendation
            recommendation.next_steps = [
                "Capture a closer or sharper frame of the key control or label.",
                "Open the relevant SOP or manual for confirmation.",
                "Delay the action until the missing evidence is available.",
            ]
            recommendation.evidence_supported = False
            recommendation.priority = "high" if risk_taxonomy.risk_level != RiskLevel.LOW else recommendation.priority
        else:
            recommendation.evidence_supported = recommendation.evidence_supported and (
                bool(document_hits) or not refined_retrieval_policy.required
            )
        choice_card = choice_manager_service.build_choice_card(
            recommendation=recommendation,
            intervention=intervention,
            clarification=clarification,
            risk_taxonomy=risk_taxonomy,
            retrieved_docs=[
                RetrievalHit(
                    document_id=item["id"],
                    title=item["title"],
                    snippet=item["snippet"],
                    score=float(item["score"]),
                    source=item["source"],
                )
                for item in document_hits
            ],
        )
        grounding_refs = grounding_service.build_grounding_refs(
            scene_observation=scene_result,
            retrieved_docs=[
                RetrievalHit(
                    document_id=item["id"],
                    title=item["title"],
                    snippet=item["snippet"],
                    score=float(item["score"]),
                    source=item["source"],
                )
                for item in document_hits
            ],
            recommendation=recommendation,
            ocr_text=ocr_text,
        )
        recommendation.grounding_refs = grounding_refs
        recommendation.choice_card = choice_card
        if choice_card is not None:
            grounding_hint = grounding_service.summarize_grounding(grounding_refs)
            if grounding_hint:
                choice_card.evidence_hint = (
                    f"{choice_card.evidence_hint} {grounding_hint}".strip()
                    if choice_card.evidence_hint
                    else grounding_hint
                )
        await event_bus.emit_event(
            session_id,
            "policy",
            {
                "clarification_required": clarification.required,
                "clarification_reason": clarification.reason,
                "clarification_question": clarification.question,
                "risk_bucket": risk_taxonomy.risk_bucket,
                "risk_reason": risk_taxonomy.reason,
                "intervention_type": recommendation.intervention_type.value,
                "choice_card_type": choice_card.card_type if choice_card is not None else None,
                "grounding_count": len(grounding_refs),
            },
            run_id=run_id,
            user_id=user_id,
        )
        await _record_audit(
            session_id=session_id,
            run_id=run_id,
            event_type="scene_policies_evaluated",
            detail={
                "clarification_required": clarification.required,
                "clarification_reason": clarification.reason,
                "risk_bucket": risk_taxonomy.risk_bucket,
                "risk_reason": risk_taxonomy.reason,
                "approval_mode": risk_taxonomy.approval_mode,
                "intervention_type": recommendation.intervention_type.value,
                "memory_context_available": bool(memory_context_text),
                "grounding_count": len(grounding_refs),
                "approval_resume_active": bool(approved_action_plan),
            },
            user_id=user_id,
        )
        await _emit_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type="scene_action_grounding",
            payload={
                "count": len(grounding_refs),
                "refs": [
                    {
                        "anchor_label": item.anchor_label,
                        "anchor_type": item.anchor_type,
                        "action_step": item.action_step,
                        "doc_title": item.doc_title,
                        "confidence": item.confidence,
                    }
                    for item in grounding_refs
                ],
            },
            user_id=user_id,
        )
        await asyncio.to_thread(
            artifact_service.record_artifact,
            session_id=session_id,
            run_id=run_id,
            artifact_type=ArtifactType.GROUNDING,
            stage="grounding",
            provider="scene_action_grounder",
            content={
                "refs": [
                    {
                        "anchor_type": item.anchor_type,
                        "anchor_label": item.anchor_label,
                        "action_step": item.action_step,
                        "rationale": item.rationale,
                        "doc_title": item.doc_title,
                        "support_snippet": item.support_snippet,
                        "confidence": item.confidence,
                    }
                    for item in grounding_refs
                ],
            },
            user_id=user_id,
        )
        await _emit_artifact(
            session_id=session_id,
            run_id=run_id,
            artifact_type="action_recommendation",
            payload={
                "title": recommendation.title,
                "risk_level": recommendation.risk_level.value,
                "priority": recommendation.priority,
                "intervention_type": recommendation.intervention_type.value,
                "uncertainty_level": recommendation.uncertainty_level,
                "approval_required": recommendation.approval_required,
                "choice_card_type": choice_card.card_type if choice_card is not None else None,
                "evidence_supported": recommendation.evidence_supported,
                "grounding_count": len(grounding_refs),
                "latency_ms": decision_latency_ms,
            },
            user_id=user_id,
        )

        await _emit_stage(
            session_id=session_id,
            run_id=run_id,
            status=RunStatus.SYNTHESIZING,
            name="approval",
            message="Evaluating explicit safety and approval policies.",
            user_id=user_id,
        )
        with Timer("approval") as timer:
            approval = await asyncio.to_thread(
                scene_pipeline_service.evaluate_approval,
                session_id=session_id,
                run_id=run_id,
                recommendation=recommendation,
                scene_observation=scene_result,
                clarification=clarification,
                risk_taxonomy=risk_taxonomy,
                choice_card=choice_card,
                grounding_refs=grounding_refs,
                ocr_text=ocr_text,
                retrieved_docs=[
                    RetrievalHit(
                        document_id=item["id"],
                        title=item["title"],
                        snippet=item["snippet"],
                        score=float(item["score"]),
                        source=item["source"],
                    )
                    for item in document_hits
                ],
                retrieved_document_count=len(document_hits),
                user_id=user_id,
            )
        approval_latency_ms = timer.sample().duration_ms
        await _record_stage_timing(
            run_id=run_id,
            stage_name="approval",
            duration_ms=approval_latency_ms,
            extra={"status": approval.status.value, "risk_bucket": risk_taxonomy.risk_bucket},
        )
        await event_bus.emit_event(
            session_id,
            "approval",
            {
                "status": approval.status.value,
                "risk_level": approval.risk_level.value,
                "reason": approval.reason,
                "risk_bucket": risk_taxonomy.risk_bucket,
                "blocked": recommendation.blocked,
            },
            run_id=run_id,
            user_id=user_id,
        )

        await _emit_stage(
            session_id=session_id,
            run_id=run_id,
            status=RunStatus.PERSISTING,
            name="persist",
            message="Persisting artifacts, scene memory, and action cards.",
            user_id=user_id,
        )
        memory_result = await asyncio.to_thread(
            scene_memory_service.persist_result,
            session_id=session_id,
            run_id=run_id,
            prompt=user_message,
            image_path=image_paths[0] if image_paths else None,
            ocr_text=ocr_text,
            scene_observation=scene_result,
            decision=recommendation,
            choice_card=choice_card,
            user_id=user_id,
        )

        final_text = _compose_final(
            transcript=transcript,
            ocr_text=ocr_text,
            scene_summary=scene_summary,
            documents=document_hits,
            decision=recommendation,
        )
        await asyncio.to_thread(
            _persist_chat,
            user_id,
            "assistant",
            final_text,
            session_id=session_id,
            run_id=run_id,
            tool_calls=[
                {"service": "speech", "used": bool(audio_paths)},
                {"service": "ocr", "used": bool(image_paths)},
                {"service": "retrieval", "document_count": len(document_hits)},
                {"service": "choice_manager", "card_type": choice_card.card_type if choice_card is not None else None},
                {"service": "approval", "status": approval.status.value},
                {"service": "memory_context", "available": bool(memory_context_text)},
                {"service": "scene_memory", **memory_result},
            ],
        )

        total_latency_ms = round((time.perf_counter() - run_started) * 1000, 2)
        final_status = RunStatus.WAITING_FOR_APPROVAL if recommendation.blocked else RunStatus.COMPLETED
        final_stage = "approval_gate" if recommendation.blocked else "completed"
        await _record_stage_timing(
            run_id=run_id,
            stage_name="total",
            duration_ms=total_latency_ms,
            extra={"status": final_status.value},
        )
        await _transition_run(
            run_id,
            status=final_status,
            current_stage=final_stage,
            route_name=plan.route_name,
            output_text=final_text,
            latency_ms=total_latency_ms,
        )
        artifact_count = len(await asyncio.to_thread(artifact_service.list_artifacts, run_id))
        await event_bus.emit_event(
            session_id,
            "final",
            {
                "text": final_text,
                "document_count": len(document_hits),
                "scene_capture_id": memory_result.get("scene_capture_id"),
                "action_card_id": memory_result.get("action_card_id"),
                "run_latency_ms": total_latency_ms,
                "run_id": run_id,
                "artifact_count": artifact_count,
                "approval_status": approval.status.value,
                "blocked": recommendation.blocked,
                "intervention_type": recommendation.intervention_type.value,
                "timings": (await asyncio.to_thread(session_manager.get_run, run_id)).get("timings_json", {}),
            },
            run_id=run_id,
            user_id=user_id,
        )

        return {
            "session_id": session_id,
            "run_id": run_id,
            "final": final_text,
            "decision": {
                "title": recommendation.title,
                "recommendation": recommendation.recommendation,
                "risk_level": recommendation.risk_level.value,
                "next_steps": recommendation.next_steps,
                "confidence": recommendation.confidence,
                "priority": recommendation.priority,
                "blocked": recommendation.blocked,
                "approval_required": recommendation.approval_required,
                "intervention_type": recommendation.intervention_type.value,
                "uncertainty_level": recommendation.uncertainty_level,
                "clarification_question": recommendation.clarification_question,
                "supporting_doc_titles": recommendation.supporting_doc_titles,
                "grounding_refs": [
                    {
                        "anchor_type": item.anchor_type,
                        "anchor_label": item.anchor_label,
                        "action_step": item.action_step,
                        "doc_title": item.doc_title,
                    }
                    for item in recommendation.grounding_refs
                ],
            },
        }
    except Exception as exc:
        if run_id is not None:
            await _record_audit(
                session_id=session_id,
                run_id=run_id,
                event_type="run_failed",
                detail={"error": f"{type(exc).__name__}: {exc}"},
                user_id=user_id,
            )
            await _transition_run(
                run_id,
                status=RunStatus.FAILED,
                current_stage="failed",
                route_name=plan.route_name,
                latency_ms=round((time.perf_counter() - run_started) * 1000, 2),
                error_message=f"{type(exc).__name__}: {exc}",
            )
        raise
