from __future__ import annotations

from typing import Any

from ..config import DEMO_USER_ID
from ..domain.runtime_models import RunStatus
from .continuation_service import continuation_service
from .scene_memory_service import scene_memory_service
from .session_manager import session_manager


class ChoiceExecutionService:
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
                    "last_option_id": normalized,
                    "continuation_run_id": handle.run_id,
                    "continuation_reason": "clarification_followup",
                    "note": note,
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

        if normalized in {"view_manual", "open_manual", "view_evidence"}:
            scene_memory_service.update_action_card(
                card_id,
                status="evidence_opened",
                context_patch={
                    **context,
                    "last_option_id": normalized,
                    "note": note,
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
                context_patch={**context, "last_option_id": normalized, "note": note},
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

        if normalized == "show_recommendation":
            scene_memory_service.update_action_card(
                card_id,
                status="acknowledged",
                context_patch={**context, "last_option_id": normalized, "note": note},
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
                context_patch={**context, "last_option_id": normalized, "note": note},
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
                context_patch={**context, "last_option_id": normalized, "note": note},
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
