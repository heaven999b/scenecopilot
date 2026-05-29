from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..domain.runtime_models import RiskLevel, SceneObservation


def _keywords(text: str, *, limit: int = 10) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in text.replace("/", " ").replace("-", " ").split():
        token = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(token) < 4 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


class ApprovalStepService:
    def normalize(self, plan: dict[str, Any] | None) -> dict[str, Any]:
        normalized = deepcopy(plan or {})
        raw_steps = normalized.get("approved_steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            next_steps = [
                str(item).strip()
                for item in normalized.get("approved_next_steps", [])
                if str(item).strip()
            ]
            raw_steps = [
                {
                    "step_id": f"approved-step-{index + 1}",
                    "title": step,
                    "ordinal": index + 1,
                    "status": "pending",
                    "approved": True,
                }
                for index, step in enumerate(next_steps)
            ]
        normalized["approved_steps"] = raw_steps
        normalized["step_cursor"] = int(normalized.get("step_cursor") or 0)
        normalized["completed_steps"] = [
            str(item).strip()
            for item in normalized.get("completed_steps", [])
            if str(item).strip()
        ]
        normalized["pending_steps"] = [
            step.get("title")
            for step in raw_steps[normalized["step_cursor"] :]
            if isinstance(step, dict) and str(step.get("title") or "").strip()
        ]
        current = None
        if 0 <= normalized["step_cursor"] < len(raw_steps):
            current = raw_steps[normalized["step_cursor"]]
        normalized["current_step"] = (
            str(current.get("title") or "").strip()
            if isinstance(current, dict)
            else None
        )
        normalized["step_state"] = str(normalized.get("step_state") or ("ready" if current else "completed"))
        focus_terms = normalized.get("resume_focus_terms")
        if not isinstance(focus_terms, list) or not focus_terms:
            focus_terms = _keywords(
                " ".join(
                    [
                        str(normalized.get("approved_title") or ""),
                        str(normalized.get("approved_recommendation") or ""),
                        " ".join(normalized.get("pending_steps") or []),
                    ]
                ),
                limit=10,
            )
        normalized["resume_focus_terms"] = focus_terms
        if not isinstance(normalized.get("resume_guard"), dict):
            normalized["resume_guard"] = {
                "requires_scene_match": True,
                "recheck_on_new_hazard": True,
                "block_on_high_uncertainty": True,
                "allow_clarification_only_on_contradiction": True,
            }
        return normalized

    def advance(self, plan: dict[str, Any] | None, *, note: str | None = None) -> dict[str, Any]:
        normalized = self.normalize(plan)
        steps = normalized["approved_steps"]
        cursor = normalized["step_cursor"]
        if cursor >= len(steps):
            normalized["step_state"] = "completed"
            normalized["current_step"] = None
            normalized["pending_steps"] = []
            return normalized

        current = steps[cursor]
        current_title = str(current.get("title") or "").strip()
        if current_title and current_title not in normalized["completed_steps"]:
            normalized["completed_steps"].append(current_title)
        current["status"] = "completed"
        if note:
            current["completion_note"] = note
        cursor += 1
        normalized["step_cursor"] = cursor
        if cursor < len(steps):
            next_step = steps[cursor]
            next_step["status"] = "ready"
            normalized["current_step"] = str(next_step.get("title") or "").strip() or None
            normalized["pending_steps"] = [
                str(step.get("title") or "").strip()
                for step in steps[cursor:]
                if str(step.get("title") or "").strip()
            ]
            normalized["step_state"] = "ready"
        else:
            normalized["current_step"] = None
            normalized["pending_steps"] = []
            normalized["step_state"] = "completed"
        return normalized

    def evaluate_resume_consistency(
        self,
        *,
        plan: dict[str, Any] | None,
        scene_observation: SceneObservation,
        ocr_text: str,
    ) -> dict[str, Any]:
        normalized = self.normalize(plan)
        resume_guard = normalized["resume_guard"]
        focus_terms = [
            str(item).strip().lower()
            for item in normalized.get("resume_focus_terms", [])
            if str(item).strip()
        ]
        scene_text = " ".join(
            part
            for part in [
                scene_observation.summary,
                ocr_text,
                scene_observation.structure.workflow_state,
                scene_observation.structure.attention_summary,
                " ".join(item.label for item in scene_observation.structure.text_layer[:4]),
                " ".join(item.label for item in scene_observation.structure.object_layer[:4]),
                " ".join(item.label for item in scene_observation.structure.hazard_layer[:4]),
                " ".join(item.label for item in scene_observation.structure.attention_targets[:4]),
            ]
            if part
        ).lower()
        matched_terms = [term for term in focus_terms if term in scene_text]
        missing_scene_match = bool(focus_terms) and not matched_terms and bool(resume_guard.get("requires_scene_match", True))
        new_hazard = scene_observation.risk_level == RiskLevel.HIGH and bool(resume_guard.get("recheck_on_new_hazard", True))
        high_uncertainty = scene_observation.uncertainty_level == "high" and bool(resume_guard.get("block_on_high_uncertainty", True))

        conflict = False
        reason = ""
        if new_hazard:
            conflict = True
            reason = "The current scene now looks high-risk, so the approved action should be re-checked before resuming."
        elif high_uncertainty:
            conflict = True
            reason = "The current scene is too uncertain to safely continue the already approved step without fresh confirmation."
        elif missing_scene_match:
            conflict = True
            reason = "The current scene no longer clearly matches the approved action focus, so the resume path should pause for re-confirmation."

        return {
            "plan": normalized,
            "conflict": conflict,
            "reason": reason,
            "matched_terms": matched_terms,
            "current_step": normalized.get("current_step"),
            "step_cursor": normalized.get("step_cursor"),
            "step_state": normalized.get("step_state"),
        }


approval_step_service = ApprovalStepService()
