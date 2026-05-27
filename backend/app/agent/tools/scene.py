from __future__ import annotations

from pathlib import Path
from typing import Any


def _classify_risk(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ("danger", "warning", "caution", "hot", "voltage", "biohazard")):
        return "high"
    if any(token in lower for token in ("careful", "sharp", "heavy", "restricted", "wet floor")):
        return "medium"
    return "low"


def _keywords(text: str, limit: int = 6) -> list[str]:
    words = []
    seen = set()
    for raw in text.replace("/", " ").replace("-", " ").split():
        token = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(token) < 4 or token in seen:
            continue
        seen.add(token)
        words.append(token)
        if len(words) >= limit:
            break
    return words


async def run_ocr(image_path: str, visible_text: str | None = None) -> dict[str, Any]:
    path = Path(image_path)
    sidecar = path.with_suffix(".txt")
    extracted = (visible_text or "").strip()
    if not extracted and sidecar.exists():
        extracted = sidecar.read_text(encoding="utf-8", errors="ignore").strip()
    if not extracted:
        extracted = (
            "No local OCR text was provided. In production this tool should call a vision "
            "model or OCR engine and return visible text from the frame."
        )
    return {
        "source": "local_stub",
        "image_path": image_path,
        "text": extracted,
        "text_blocks": extracted.splitlines()[:8],
    }


async def describe_scene(image_path: str, prompt: str, ocr_text: str = "") -> dict[str, Any]:
    path = Path(image_path)
    context = f"{prompt} {ocr_text} {path.name}".strip()
    lower = context.lower()
    tags = _keywords(context)
    risk_level = _classify_risk(context)

    if "menu" in lower or "price" in lower:
        summary = "The frame likely contains a menu or pricing board, so reading and summarizing visible text is the main task."
    elif "manual" in lower or "instruction" in lower or "label" in lower:
        summary = "The frame appears to contain instructions or labels that should be read before acting."
    elif any(token in lower for token in ("panel", "switch", "button", "machine", "device")):
        summary = "The frame likely shows a device or control surface, so the best next step is to identify the controls and verify safe operation."
    else:
        summary = "The frame needs general inspection, combining visible text with scene understanding before recommending an action."

    return {
        "source": "local_stub",
        "image_path": image_path,
        "summary": summary,
        "risk_level": risk_level,
        "tags": tags,
    }


async def make_decision(
    prompt: str,
    scene_summary: str,
    ocr_text: str = "",
    document_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    lower = f"{prompt} {scene_summary} {ocr_text}".lower()
    documents = document_matches or []
    risk_level = _classify_risk(lower)
    next_steps: list[str]

    if any(token in lower for token in ("read", "translate", "text")):
        title = "Read the visible text first"
        recommendation = "Focus on extracting the visible text, then summarize the instructions or warnings before doing anything else."
        next_steps = [
            "Capture or stabilize a clearer close-up if the text is blurry.",
            "Read the headings, warnings, and numbered steps in order.",
            "If the text is procedural, compare it against the uploaded manual before acting.",
        ]
    elif risk_level == "high":
        title = "Pause and verify safety"
        recommendation = "The frame suggests a potentially risky situation. Stop before interacting and verify the relevant safety checklist."
        next_steps = [
            "Do not touch the device or area until warnings are confirmed.",
            "Check the matching SOP or manual section.",
            "Escalate to a trained operator if the warning state is unclear.",
        ]
    else:
        title = "Inspect, confirm, then act"
        recommendation = "Use the visible cues and any matching documents to confirm the object or situation before taking the next step."
        next_steps = [
            "Identify the object, label, or panel in view.",
            "Confirm any matching instructions from uploaded documents.",
            "Proceed with the lowest-risk next action and keep capturing context if needed.",
        ]

    if documents:
        recommendation += f" I found {len(documents)} potentially relevant uploaded document(s) to cross-check."

    return {
        "source": "local_stub",
        "title": title,
        "recommendation": recommendation,
        "risk_level": risk_level,
        "priority": "high" if risk_level == "high" else "medium",
        "confidence": 0.73 if documents or ocr_text else 0.58,
        "next_steps": next_steps,
    }
