from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from ..config import DEMO_USER_ID
from ..db import conn_ctx, get_conn, row_to_dict
from ..domain.runtime_models import (
    ActionRecommendation,
    ChoiceCard,
    MemoryLayers,
    MemoryScope,
    SceneObservation,
)
from .audit_service import audit_service


class SceneMemoryService:
    def _build_choice_preference_summary(self, recent_choices: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {
            "capture_close_up": 0,
            "view_evidence": 0,
            "view_manual": 0,
            "open_manual": 0,
            "defer": 0,
            "not_now": 0,
            "request_approval": 0,
            "cancel": 0,
        }
        for item in recent_choices:
            context = item.get("context_json") or {}
            option_id = str(context.get("last_option_id") or "").strip().lower()
            if option_id in counts:
                counts[option_id] += 1
        preference = "neutral"
        if counts["view_evidence"] + counts["view_manual"] + counts["open_manual"] >= 2:
            preference = "evidence_first"
        elif counts["capture_close_up"] >= 2:
            preference = "close_up_first"
        elif counts["defer"] + counts["not_now"] >= 2:
            preference = "defer_first"
        elif counts["request_approval"] >= 1:
            preference = "approval_seeking"
        return {
            "preference": preference,
            "counts": counts,
        }

    def _build_operator_control_state(self, recent_choices: list[dict[str, Any]]) -> dict[str, Any]:
        family_counts = {
            "clarification": 0,
            "evidence": 0,
            "approval": 0,
            "defer": 0,
            "abort": 0,
            "accept_guidance": 0,
        }
        status_counts: dict[str, int] = {}
        clarification_followthrough = 0
        for item in recent_choices:
            context = item.get("context_json") or {}
            option_id = str(context.get("last_option_id") or "").strip().lower()
            status = str(item.get("status") or "").strip().lower() or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
            if option_id == "capture_close_up":
                family_counts["clarification"] += 1
                if status == "continued":
                    clarification_followthrough += 1
            elif option_id in {"view_evidence", "view_manual", "open_manual"}:
                family_counts["evidence"] += 1
            elif option_id == "request_approval":
                family_counts["approval"] += 1
            elif option_id in {"defer", "not_now"}:
                family_counts["defer"] += 1
            elif option_id == "cancel":
                family_counts["abort"] += 1
            elif option_id == "show_recommendation":
                family_counts["accept_guidance"] += 1

        preference_summary = self._build_choice_preference_summary(recent_choices)
        control_mode = "balanced_control"
        if family_counts["evidence"] >= 2:
            control_mode = "evidence_control"
        elif clarification_followthrough >= 1:
            control_mode = "clarify_with_image"
        elif family_counts["approval"] >= 1:
            control_mode = "approval_control"
        elif family_counts["defer"] >= 2:
            control_mode = "defer_control"
        elif family_counts["accept_guidance"] >= 2:
            control_mode = "direct_guidance"

        followthrough_level = "low"
        if family_counts["accept_guidance"] + clarification_followthrough + family_counts["approval"] >= 2:
            followthrough_level = "high"
        elif any(value > 0 for value in family_counts.values()):
            followthrough_level = "medium"

        return {
            "preference": preference_summary["preference"],
            "control_mode": control_mode,
            "followthrough_level": followthrough_level,
            "clarification_followthrough": clarification_followthrough,
            "family_counts": family_counts,
            "status_counts": status_counts,
            "counts": preference_summary["counts"],
        }

    def build_memory_layers(
        self,
        *,
        session_id: str,
        prompt: str,
        ocr_text: str,
        scene_observation: SceneObservation,
        decision: ActionRecommendation,
    ) -> MemoryLayers:
        recent_captures = self.list_recent_session_captures(session_id, limit=3)
        recent_choices = self.list_recent_session_choices(session_id, limit=3)
        previous_capture = recent_captures[0] if recent_captures else None
        previous_context = previous_capture.get("context_json") if previous_capture else {}
        previous_structure = previous_context.get("scene_structure") if isinstance(previous_context, dict) else {}
        scene_change_memory = {
            "scope": MemoryScope.SCENE_CHANGE.value,
            "changed_since_last_capture": bool(previous_capture),
            "previous_scene_summary": previous_capture.get("scene_summary") if previous_capture else None,
            "previous_risk_level": previous_capture.get("risk_level") if previous_capture else None,
            "previous_workflow_state": previous_structure.get("workflow_state") if isinstance(previous_structure, dict) else None,
            "current_scene_summary": scene_observation.summary,
            "current_risk_level": decision.risk_level.value,
            "current_workflow_state": scene_observation.structure.workflow_state,
            "temporal_delta_summary": scene_observation.structure.temporal_delta_summary,
        }
        run_memory = {
            "scope": MemoryScope.RUN.value,
            "prompt": prompt,
            "ocr_preview": ocr_text[:160],
            "scene_summary": scene_observation.summary,
            "risk_level": decision.risk_level.value,
            "uncertainty_level": decision.uncertainty_level,
            "intervention_type": decision.intervention_type.value,
            "supporting_doc_titles": decision.supporting_doc_titles,
            "workflow_state": scene_observation.structure.workflow_state,
            "attention_summary": scene_observation.structure.attention_summary,
            "attention_targets": [item.label for item in scene_observation.structure.attention_targets[:3]],
        }
        session_memory = {
            "scope": MemoryScope.SESSION.value,
            "recent_scene_summaries": [
                {
                    "scene_summary": item.get("scene_summary"),
                    "risk_level": item.get("risk_level"),
                    "workflow_state": ((item.get("context_json") or {}).get("scene_structure") or {}).get("workflow_state"),
                    "created_at": item.get("created_at"),
                }
                for item in recent_captures[:3]
            ],
        }
        preference_summary = self._build_choice_preference_summary(recent_choices)
        operator_control_state = self._build_operator_control_state(recent_choices)
        user_choice_memory = {
            "scope": MemoryScope.USER_CHOICE.value,
            "preference_summary": preference_summary,
            "operator_control_state": operator_control_state,
            "recent_choice_cards": [
                {
                    "title": item.get("title"),
                    "card_type": item.get("card_type"),
                    "status": item.get("status"),
                    "options": item.get("options_json"),
                    "last_option_id": (item.get("context_json") or {}).get("last_option_id"),
                    "feedback_signal": (item.get("context_json") or {}).get("feedback_signal"),
                }
                for item in recent_choices[:3]
            ],
        }
        return MemoryLayers(
            run_memory=run_memory,
            session_memory=session_memory,
            scene_change_memory=scene_change_memory,
            user_choice_memory=user_choice_memory,
        )

    def summarize_session_memory(self, session_id: str, *, limit: int = 2) -> str:
        captures = self.list_recent_session_captures(session_id, limit=limit)
        parts: list[str] = []
        for item in captures[:limit]:
            summary = str(item.get("scene_summary") or "").strip()
            risk_level = str(item.get("risk_level") or "").strip()
            workflow_state = str((((item.get("context_json") or {}).get("scene_structure") or {}).get("workflow_state")) or "").strip()
            if summary:
                prefix = f"{risk_level}: {summary}"
                if workflow_state:
                    prefix += f" [{workflow_state}]"
                parts.append(prefix)
        recent_choices = self.list_recent_session_choices(session_id, limit=limit)
        preference_summary = self._build_choice_preference_summary(recent_choices[:limit])
        operator_control_state = self._build_operator_control_state(recent_choices[:limit])
        for item in recent_choices[:limit]:
            context = item.get("context_json") or {}
            option_id = str(context.get("last_option_id") or "").strip()
            status = str(item.get("status") or "").strip()
            title = str(item.get("title") or "").strip()
            if option_id:
                parts.append(f"choice:{option_id} -> {status} on {title}")
        preference = str(preference_summary.get("preference") or "").strip()
        if preference and preference != "neutral":
            parts.append(f"operator_preference:{preference}")
        control_mode = str(operator_control_state.get("control_mode") or "").strip()
        if control_mode:
            parts.append(f"operator_control_mode:{control_mode}")
        followthrough = str(operator_control_state.get("followthrough_level") or "").strip()
        if followthrough:
            parts.append(f"operator_followthrough:{followthrough}")
        return " | ".join(parts)

    def get_operator_control_state(self, session_id: str, *, limit: int = 4) -> dict[str, Any]:
        recent_choices = self.list_recent_session_choices(session_id, limit=limit)
        return self._build_operator_control_state(recent_choices[:limit])

    def persist_result(
        self,
        *,
        session_id: str,
        run_id: str,
        prompt: str,
        image_path: str | None,
        ocr_text: str,
        scene_observation: SceneObservation,
        decision: ActionRecommendation,
        choice_card: ChoiceCard | None,
        user_id: int = DEMO_USER_ID,
    ) -> dict[str, int | None]:
        memory_layers = self.build_memory_layers(
            session_id=session_id,
            prompt=prompt,
            ocr_text=ocr_text,
            scene_observation=scene_observation,
            decision=decision,
        )
        payload = {
            "title": decision.title,
            "recommendation": decision.recommendation,
            "risk_level": decision.risk_level.value,
            "next_steps": decision.next_steps,
            "confidence": decision.confidence,
            "priority": decision.priority,
            "blocked": decision.blocked,
            "approval_required": decision.approval_required,
            "intervention_type": decision.intervention_type.value,
            "uncertainty_level": decision.uncertainty_level,
            "clarification_question": decision.clarification_question,
            "supporting_doc_titles": decision.supporting_doc_titles,
            "grounding_refs": [
                {
                    "anchor_type": item.anchor_type,
                    "anchor_label": item.anchor_label,
                    "action_step": item.action_step,
                    "rationale": item.rationale,
                    "doc_title": item.doc_title,
                    "support_snippet": item.support_snippet,
                    "confidence": item.confidence,
                }
                for item in decision.grounding_refs
            ],
            "choice_card": {
                "card_type": choice_card.card_type,
                "headline": choice_card.headline,
                "rationale": choice_card.rationale,
                "options": [
                    {
                        "option_id": option.option_id,
                        "label": option.label,
                        "description": option.description,
                        "requires_confirmation": option.requires_confirmation,
                    }
                    for option in choice_card.options
                ],
                "evidence_hint": choice_card.evidence_hint,
                "cancellable": choice_card.cancellable,
                "deferrable": choice_card.deferrable,
            } if choice_card is not None else None,
        }
        with conn_ctx() as conn:
            cur = conn.execute(
                """
                INSERT INTO scene_captures
                  (user_id, session_id, run_id, image_path, prompt, ocr_text, scene_summary, risk_level, decisions_json, context_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    run_id,
                    image_path,
                    prompt,
                    ocr_text,
                    scene_observation.summary,
                    decision.risk_level.value,
                    json.dumps([payload], default=str),
                    json.dumps({
                        "run_memory": memory_layers.run_memory,
                        "session_memory": memory_layers.session_memory,
                        "scene_change_memory": memory_layers.scene_change_memory,
                        "user_choice_memory": memory_layers.user_choice_memory,
                        "scene_structure": asdict(scene_observation.structure),
                        "grounding_refs": [
                            {
                                "anchor_label": item.anchor_label,
                                "action_step": item.action_step,
                                "doc_title": item.doc_title,
                            }
                            for item in decision.grounding_refs
                        ],
                    }, default=str),
                ),
            )
            capture_id = int(cur.lastrowid)
            card_id: int | None = None
            if decision.title and decision.recommendation:
                card = conn.execute(
                    """
                    INSERT INTO action_cards (user_id, scene_capture_id, run_id, title, detail, card_type, options_json, context_json, priority, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (
                        user_id,
                        capture_id,
                        run_id,
                        decision.title,
                        decision.recommendation,
                        choice_card.card_type if choice_card is not None else decision.intervention_type.value,
                        json.dumps([
                            {
                                "option_id": option.option_id,
                                "label": option.label,
                                "description": option.description,
                                "requires_confirmation": option.requires_confirmation,
                            }
                            for option in (choice_card.options if choice_card is not None else [])
                        ], default=str),
                        json.dumps({
                            "memory_scope": [
                                MemoryScope.RUN.value,
                                MemoryScope.SESSION.value,
                                MemoryScope.SCENE_CHANGE.value,
                                MemoryScope.USER_CHOICE.value,
                            ],
                            "headline": choice_card.headline if choice_card is not None else decision.title,
                            "rationale": choice_card.rationale if choice_card is not None else decision.recommendation,
                        }, default=str),
                        decision.priority,
                    ),
                )
                card_id = int(card.lastrowid)
        audit_service.record(
            session_id=session_id,
            run_id=run_id,
            event_type="scene_memory_persisted",
            detail={
                "scene_capture_id": capture_id,
                "action_card_id": card_id,
                "memory_layers": {
                    "run": bool(memory_layers.run_memory),
                    "session": bool(memory_layers.session_memory),
                    "scene_change": bool(memory_layers.scene_change_memory),
                    "user_choice": bool(memory_layers.user_choice_memory),
                },
            },
            user_id=user_id,
        )
        return {"scene_capture_id": capture_id, "action_card_id": card_id}

    def list_recent_session_captures(self, session_id: str, *, limit: int = 4) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, scene_summary, risk_level, context_json, created_at
                FROM scene_captures
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def list_recent_session_choices(self, session_id: str, *, limit: int = 4) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT action_cards.id, action_cards.title, action_cards.card_type, action_cards.options_json, action_cards.status, action_cards.created_at
                       , action_cards.context_json
                FROM action_cards
                JOIN scene_captures ON scene_captures.id = action_cards.scene_capture_id
                WHERE scene_captures.session_id = ?
                ORDER BY action_cards.id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def list_scene_captures(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, image_path, prompt, ocr_text, scene_summary, risk_level, decisions_json, context_json, created_at
                FROM scene_captures
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def list_action_cards(self, run_id: str) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, scene_capture_id, title, detail, card_type, options_json, context_json, priority, status, created_at
                FROM action_cards
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def get_action_card(self, card_id: int) -> dict[str, Any] | None:
        conn = get_conn()
        try:
            row = conn.execute(
                """
                SELECT id, scene_capture_id, run_id, title, detail, card_type, options_json, context_json, priority, status, created_at
                FROM action_cards
                WHERE id = ?
                """,
                (card_id,),
            ).fetchone()
        finally:
            conn.close()
        return row_to_dict(row) if row is not None else None

    def get_scene_capture(self, capture_id: int) -> dict[str, Any] | None:
        conn = get_conn()
        try:
            row = conn.execute(
                """
                SELECT id, session_id, run_id, image_path, prompt, ocr_text, scene_summary, risk_level, decisions_json, context_json, created_at
                FROM scene_captures
                WHERE id = ?
                """,
                (capture_id,),
            ).fetchone()
        finally:
            conn.close()
        return row_to_dict(row) if row is not None else None

    def latest_decision_payload_for_run(self, run_id: str) -> dict[str, Any] | None:
        conn = get_conn()
        try:
            row = conn.execute(
                """
                SELECT decisions_json
                FROM scene_captures
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        decisions = row_to_dict(row).get("decisions_json") or []
        if not decisions:
            return None
        first = decisions[0]
        return first if isinstance(first, dict) else None

    def update_action_cards_status(self, run_id: str, *, status: str) -> None:
        with conn_ctx() as conn:
            conn.execute(
                """
                UPDATE action_cards
                SET status = ?
                WHERE run_id = ?
                """,
                (status, run_id),
            )

    def update_action_card(
        self,
        card_id: int,
        *,
        status: str | None = None,
        context_patch: dict[str, Any] | None = None,
    ) -> None:
        with conn_ctx() as conn:
            row = conn.execute(
                """
                SELECT context_json, status
                FROM action_cards
                WHERE id = ?
                """,
                (card_id,),
            ).fetchone()
            if row is None:
                return
            current_context = {}
            raw_context = row["context_json"]
            if raw_context:
                try:
                    current_context = json.loads(raw_context)
                except json.JSONDecodeError:
                    current_context = {}
            if context_patch:
                current_context.update(context_patch)
            conn.execute(
                """
                UPDATE action_cards
                SET status = COALESCE(?, status),
                    context_json = ?
                WHERE id = ?
                """,
                (status, json.dumps(current_context, default=str), card_id),
            )


scene_memory_service = SceneMemoryService()
