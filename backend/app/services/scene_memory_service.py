from __future__ import annotations

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
        scene_change_memory = {
            "scope": MemoryScope.SCENE_CHANGE.value,
            "changed_since_last_capture": bool(previous_capture),
            "previous_scene_summary": previous_capture.get("scene_summary") if previous_capture else None,
            "previous_risk_level": previous_capture.get("risk_level") if previous_capture else None,
            "current_scene_summary": scene_observation.summary,
            "current_risk_level": decision.risk_level.value,
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
        }
        session_memory = {
            "scope": MemoryScope.SESSION.value,
            "recent_scene_summaries": [
                {
                    "scene_summary": item.get("scene_summary"),
                    "risk_level": item.get("risk_level"),
                    "created_at": item.get("created_at"),
                }
                for item in recent_captures[:3]
            ],
        }
        user_choice_memory = {
            "scope": MemoryScope.USER_CHOICE.value,
            "recent_choice_cards": [
                {
                    "title": item.get("title"),
                    "card_type": item.get("card_type"),
                    "status": item.get("status"),
                    "options": item.get("options_json"),
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
        if not captures:
            return ""
        parts: list[str] = []
        for item in captures[:limit]:
            summary = str(item.get("scene_summary") or "").strip()
            risk_level = str(item.get("risk_level") or "").strip()
            if summary:
                parts.append(f"{risk_level}: {summary}")
        return " | ".join(parts)

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
                        "scene_structure": {
                            "layout_summary": scene_observation.structure.layout_summary,
                            "primary_entry_points": [item.label for item in scene_observation.structure.primary_entry_points],
                            "text_regions": [item.label for item in scene_observation.structure.text_regions],
                            "action_controls": [item.label for item in scene_observation.structure.action_controls],
                            "hazard_cues": [item.label for item in scene_observation.structure.hazard_cues],
                            "salient_elements": [item.label for item in scene_observation.structure.salient_elements],
                        },
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
                SELECT id, scene_summary, risk_level, created_at
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


scene_memory_service = SceneMemoryService()
