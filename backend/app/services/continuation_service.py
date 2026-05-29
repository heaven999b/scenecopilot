from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import DEMO_USER_ID
from ..domain.runtime_models import RunStatus
from ..orchestration.planner import build_default_plan
from .approval_step_service import approval_step_service
from .scene_memory_service import scene_memory_service
from .session_manager import SessionHandle, session_manager


def _existing_paths(values: list[str]) -> list[str]:
    return [path for path in values if path and Path(path).exists()]


def _focus_terms(*values: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        for raw in value.replace("/", " ").replace("-", " ").split():
            token = "".join(ch for ch in raw.lower() if ch.isalnum())
            if len(token) < 4 or token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= 10:
                return terms
    return terms


class ContinuationService:
    def _build_approved_action_plan(
        self,
        *,
        decision_payload: dict[str, Any],
        approval_packet_override: dict[str, Any] | None,
        reviewer_note: str | None,
    ) -> dict[str, Any]:
        packet = dict(approval_packet_override or {})
        supporting_docs = list(decision_payload.get("supporting_doc_titles") or [])
        if not supporting_docs:
            supporting_docs = [
                str(item.get("title") or "").strip()
                for item in packet.get("supporting_docs", [])
                if str(item.get("title") or "").strip()
            ]
        grounding_refs = list(decision_payload.get("grounding_refs") or [])
        if not grounding_refs:
            grounding_refs = list(packet.get("grounding_refs") or [])
        next_steps = [str(item).strip() for item in decision_payload.get("next_steps", []) if str(item).strip()]
        if not next_steps:
            next_steps = [str(item).strip() for item in packet.get("next_steps", []) if str(item).strip()]
        approved_title = str(decision_payload.get("title") or packet.get("recommended_action") or "").strip()
        approved_recommendation = str(decision_payload.get("recommendation") or packet.get("recommended_action") or "").strip()
        step_objects = [
            {
                "step_id": f"approved-step-{index + 1}",
                "title": step,
                "ordinal": index + 1,
                "status": "pending",
                "approved": True,
            }
            for index, step in enumerate(next_steps)
        ]
        return {
            "approved_title": approved_title,
            "approved_recommendation": approved_recommendation,
            "approved_next_steps": next_steps,
            "approved_steps": step_objects,
            "step_cursor": 0,
            "current_step": next_steps[0] if next_steps else approved_recommendation or approved_title or None,
            "pending_steps": next_steps,
            "completed_steps": [],
            "step_state": "ready",
            "resume_guard": {
                "requires_scene_match": True,
                "recheck_on_new_hazard": True,
                "block_on_high_uncertainty": True,
                "allow_clarification_only_on_contradiction": True,
            },
            "resume_focus_terms": _focus_terms(
                approved_title,
                approved_recommendation,
                " ".join(next_steps),
            ),
            "supporting_doc_titles": supporting_docs,
            "grounding_refs": grounding_refs,
            "reviewer_note": (reviewer_note or "").strip() or None,
            "risk_bucket": packet.get("risk_bucket"),
            "uncertainty_level": packet.get("uncertainty_level"),
        }

    def build_payload(
        self,
        source_run: dict[str, Any],
        *,
        continuation_reason: str,
        source_option_id: str | None = None,
        prompt_override: str | None = None,
        requires_media: bool = False,
        approval_packet_override: dict[str, Any] | None = None,
        approved_action_plan_override: dict[str, Any] | None = None,
        reviewer_note: str | None = None,
    ) -> dict[str, Any]:
        input_json = dict(source_run.get("input_json") or {})
        decision_payload = scene_memory_service.latest_decision_payload_for_run(source_run["id"]) or {}
        image_paths = _existing_paths(list(input_json.get("image_paths") or []))
        audio_paths = _existing_paths(list(input_json.get("audio_paths") or []))

        image_path = str(input_json.get("image_path") or "").strip()
        if image_path and Path(image_path).exists() and image_path not in image_paths:
            image_paths.append(image_path)
        audio_path = str(input_json.get("audio_path") or "").strip()
        if audio_path and Path(audio_path).exists() and audio_path not in audio_paths:
            audio_paths.append(audio_path)

        payload = dict(input_json)
        payload.update({
            "parent_run_id": source_run["id"],
            "continuation_reason": continuation_reason,
            "source_option_id": source_option_id,
            "source_run_status": source_run.get("status"),
            "source_run_trigger": source_run.get("trigger"),
            "parent_user_message": source_run.get("user_message"),
            "parent_output_text": source_run.get("output_text"),
            "parent_decision": decision_payload,
            "required_followup_media": "image" if requires_media else None,
        })
        if continuation_reason == "approval_resume":
            if approved_action_plan_override is not None:
                payload["approved_action_plan"] = approval_step_service.normalize(approved_action_plan_override)
            else:
                payload["approved_action_plan"] = approval_step_service.normalize(
                    self._build_approved_action_plan(
                        decision_payload=decision_payload,
                        approval_packet_override=approval_packet_override,
                        reviewer_note=reviewer_note,
                    )
                )
            payload["approval_resume_active"] = True
        payload["image_paths"] = [] if requires_media else image_paths
        payload["audio_paths"] = audio_paths
        payload["seed_image_paths"] = image_paths
        if prompt_override:
            payload["continuation_prompt_override"] = prompt_override
        return payload

    def start_followup_run(
        self,
        *,
        source_run: dict[str, Any],
        continuation_reason: str,
        source_option_id: str | None = None,
        prompt_override: str | None = None,
        requires_media: bool = False,
        trigger: str,
        user_id: int = DEMO_USER_ID,
        approval_packet_override: dict[str, Any] | None = None,
        approved_action_plan_override: dict[str, Any] | None = None,
        reviewer_note: str | None = None,
    ) -> tuple[SessionHandle, dict[str, Any]]:
        payload = self.build_payload(
            source_run,
            continuation_reason=continuation_reason,
            source_option_id=source_option_id,
            prompt_override=prompt_override,
            requires_media=requires_media,
            approval_packet_override=approval_packet_override,
            approved_action_plan_override=approved_action_plan_override,
            reviewer_note=reviewer_note,
        )
        effective_prompt = prompt_override or source_run["user_message"]
        plan = build_default_plan(
            user_message=effective_prompt,
            has_image=bool(payload.get("image_paths")),
            has_audio=bool(payload.get("audio_paths") or payload.get("prefetched_transcript")),
        )
        handle = session_manager.start_run(
            user_id=user_id,
            user_message=effective_prompt,
            session_id=source_run["session_id"],
            trigger=trigger,
            image_count=len(payload.get("image_paths") or []),
            input_payload=payload,
            plan=plan,
        )
        if requires_media:
            session_manager.update_run_status(
                handle.run_id,
                status=RunStatus.AWAITING_INPUT,
                current_stage="awaiting_followup_media",
            )
        return handle, payload


continuation_service = ContinuationService()
