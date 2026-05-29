from __future__ import annotations

from typing import Any

from ..config import DEMO_USER_ID
from ..domain.runtime_models import RunStatus
from .approval_step_service import approval_step_service
from .continuation_service import continuation_service
from .scene_memory_service import scene_memory_service
from .session_manager import session_manager


class ChoiceExecutionService:
    def _feedback_context_patch(self, *, option_id: str, status: str, note: str | None) -> dict[str, Any]:
        normalized = option_id.strip().lower()
        family = "unknown"
        if normalized == "capture_close_up":
            family = "clarification"
        elif normalized in {"view_manual", "open_manual", "view_evidence"}:
            family = "evidence"
        elif normalized == "request_approval":
            family = "approval"
        elif normalized in {"defer", "not_now"}:
            family = "defer"
        elif normalized == "cancel":
            family = "abort"
        elif normalized == "show_recommendation":
            family = "accept_guidance"
        return {
            "last_option_id": normalized,
            "note": note,
            "feedback_family": family,
            "feedback_outcome": status,
            "feedback_signal": {
                "option_id": normalized,
                "family": family,
                "outcome": status,
                "note": note,
            },
        }

    def execute(
        self,
        *,
        card_id: int,
        option_id: str,
        note: str | None = None,
        user_id: int = DEMO_USER_ID,
    ) -> dict[str, Any]:
        card = scene_memory_service.get_action_card(card_id)
        if card is None:
            raise ValueError("Action card not found")
        run = session_manager.get_run(str(card["run_id"]))
        if run is None:
            raise ValueError("Source run not found")
        capture = scene_memory_service.get_scene_capture(int(card["scene_capture_id"])) if card.get("scene_capture_id") else None
        decision = scene_memory_service.latest_decision_payload_for_run(str(card["run_id"])) or {}
        context = dict(card.get("context_json") or {})
        evidence = {
            "source_run_id": card["run_id"],
            "scene_capture_id": card.get("scene_capture_id"),
            "supporting_doc_titles": decision.get("supporting_doc_titles") or [],
            "grounding_refs": decision.get("grounding_refs") or [],
            "scene_summary": capture.get("scene_summary") if capture else None,
            "ocr_text": capture.get("ocr_text") if capture else None,
            "approved_action_plan": run.get("input_json", {}).get("approved_action_plan") if isinstance(run.get("input_json"), dict) else None,
        }

        normalized = option_id.strip().lower()
        if normalized == "capture_close_up":
            handle, payload = continuation_service.start_followup_run(
                source_run=run,
                continuation_reason="clarification_followup",
                source_option_id=normalized,
                requires_media=True,
                trigger="clarification_followup",
                user_id=user_id,
            )
            scene_memory_service.update_action_card(
                card_id,
                status="continued",
                context_patch={
                    **context,
                    "continuation_run_id": handle.run_id,
                    "continuation_reason": "clarification_followup",
                    **self._feedback_context_patch(option_id=normalized, status="continued", note=note),
                },
            )
            return {
                "card": card,
                "run": run,
                "status": "continued",
                "message": "A clarification follow-up run is waiting for a closer image or sharper frame.",
                "continuation_run_id": handle.run_id,
                "continuation_state": RunStatus.AWAITING_INPUT.value,
                "evidence": {
                    **evidence,
                    "required_followup_media": payload.get("required_followup_media"),
                },
            }

        if normalized == "mark_step_done":
            input_json = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
            approved_action_plan = input_json.get("approved_action_plan") if isinstance(input_json.get("approved_action_plan"), dict) else None
            if approved_action_plan is None:
                raise ValueError("No approved action plan is attached to this run")
            advanced_plan = approval_step_service.advance(approved_action_plan, note=note)
            if advanced_plan.get("step_state") == "completed":
                scene_memory_service.update_action_card(
                    card_id,
                    status="completed",
                    context_patch={
                        **context,
                        **self._feedback_context_patch(option_id=normalized, status="completed", note=note),
                        "approved_action_plan": advanced_plan,
                    },
                )
                return {
                    "card": card,
                    "run": run,
                    "status": "completed",
                    "message": "The approved action plan is fully completed.",
                    "continuation_run_id": None,
                    "continuation_state": RunStatus.COMPLETED.value,
                    "evidence": {
                        **evidence,
                        "approved_action_plan": advanced_plan,
                    },
                }
            handle, payload = continuation_service.start_followup_run(
                source_run=run,
                continuation_reason="approval_resume",
                source_option_id=normalized,
                requires_media=False,
                trigger="approval_resume_step",
                user_id=user_id,
                approved_action_plan_override=advanced_plan,
            )
            scene_memory_service.update_action_card(
                card_id,
                status="step_advanced",
                context_patch={
                    **context,
                    "continuation_run_id": handle.run_id,
                    "continuation_reason": "approval_resume",
                    "approved_action_plan": advanced_plan,
                    **self._feedback_context_patch(option_id=normalized, status="step_advanced", note=note),
                },
            )
            return {
                "card": card,
                "run": run,
                "status": "step_advanced",
                "message": f"Advanced the approved plan to: {advanced_plan.get('current_step') or 'completed'}",
                "continuation_run_id": handle.run_id,
                "continuation_state": RunStatus.QUEUED.value,
                "queue_ready": True,
                "queue_trigger": "approval_resume_step",
                "continuation_payload": payload,
                "evidence": {
                    **evidence,
                    "approved_action_plan": advanced_plan,
                },
            }

        if normalized in {"view_manual", "open_manual", "view_evidence"}:
            scene_memory_service.update_action_card(
                card_id,
                status="evidence_opened",
                context_patch={
                    **context,
                    **self._feedback_context_patch(option_id=normalized, status="evidence_opened", note=note),
                },
            )
            return {
                "card": card,
                "run": run,
                "status": "evidence_opened",
                "message": "Evidence and supporting references are ready to inspect.",
                "continuation_run_id": None,
                "continuation_state": None,
                "evidence": evidence,
            }

        if normalized in {"not_now", "defer"}:
            scene_memory_service.update_action_card(
                card_id,
                status="deferred",
                context_patch={**context, **self._feedback_context_patch(option_id=normalized, status="deferred", note=note)},
            )
            return {
                "card": card,
                "run": run,
                "status": "deferred",
                "message": "The suggestion was deferred and can be revisited later.",
                "continuation_run_id": None,
                "continuation_state": None,
                "evidence": evidence,
            }

        if normalized == "pause_resume":
            scene_memory_service.update_action_card(
                card_id,
                status="paused",
                context_patch={**context, **self._feedback_context_patch(option_id=normalized, status="paused", note=note)},
            )
            return {
                "card": card,
                "run": run,
                "status": "paused",
                "message": "The approved resume path was paused and can be resumed later.",
                "continuation_run_id": None,
                "continuation_state": None,
                "evidence": evidence,
            }

        if normalized == "show_recommendation":
            scene_memory_service.update_action_card(
                card_id,
                status="acknowledged",
                context_patch={**context, **self._feedback_context_patch(option_id=normalized, status="acknowledged", note=note)},
            )
            return {
                "card": card,
                "run": run,
                "status": "acknowledged",
                "message": "The recommendation was acknowledged.",
                "continuation_run_id": None,
                "continuation_state": None,
                "evidence": evidence,
            }

        if normalized == "request_approval":
            scene_memory_service.update_action_card(
                card_id,
                status="approval_requested",
                context_patch={**context, **self._feedback_context_patch(option_id=normalized, status="approval_requested", note=note)},
            )
            return {
                "card": card,
                "run": run,
                "status": "approval_requested",
                "message": "The action remains at the approval gate pending operator review.",
                "continuation_run_id": None,
                "continuation_state": RunStatus.WAITING_FOR_APPROVAL.value,
                "evidence": evidence,
            }

        if normalized == "cancel":
            scene_memory_service.update_action_card(
                card_id,
                status="cancel_requested",
                context_patch={**context, **self._feedback_context_patch(option_id=normalized, status="cancel_requested", note=note)},
            )
            return {
                "card": card,
                "run": run,
                "status": "cancel_requested",
                "message": "The action was marked for cancellation.",
                "continuation_run_id": None,
                "continuation_state": None,
                "evidence": evidence,
            }

        raise ValueError(f"Unsupported option_id: {option_id}")


choice_execution_service = ChoiceExecutionService()
