from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent.core import run_agent
from ..config import DATA_DIR, DEMO_USER_ID
from ..domain.runtime_models import FrameRef
from ..orchestration.planner import build_default_plan
from ..providers.local import (
    LocalDecisionProvider,
    LocalHashEmbeddingProvider,
    LocalOCRProvider,
    LocalVisionProvider,
    NoopSpeechProvider,
    SQLiteRetrievalProvider,
)
from ..seed import seed
from ..services.approval_service import approval_service
from ..services.artifact_service import artifact_service
from ..services.pipeline_service import scene_pipeline_service
from ..services.session_manager import session_manager

EVAL_DIR = DATA_DIR / "evals"


@dataclass(slots=True)
class EvalCase:
    name: str
    user_message: str
    image_path: str
    expected_ocr_contains: str
    expected_risk_level: str
    expected_approval_required: bool
    expected_doc_title: str | None = None


CASES = [
    EvalCase(
        name="warning_panel",
        user_message="Read this warning label and tell me if it is safe to open the panel or what I should do next.",
        image_path=str(EVAL_DIR / "warning_panel.png"),
        expected_ocr_contains="High voltage",
        expected_risk_level="high",
        expected_approval_required=True,
        expected_doc_title="Forklift Safety SOP",
    ),
    EvalCase(
        name="menu_board",
        user_message="Read this menu and summarize the sections for me.",
        image_path=str(EVAL_DIR / "menu_board.png"),
        expected_ocr_contains="Soup of the Day",
        expected_risk_level="low",
        expected_approval_required=False,
    ),
    EvalCase(
        name="wearable_setup",
        user_message="Compare this setup card against the wearable quickstart and tell me the right next steps.",
        image_path=str(EVAL_DIR / "wearable_setup.png"),
        expected_ocr_contains="Hold steady",
        expected_risk_level="low",
        expected_approval_required=False,
        expected_doc_title="Wearable Quickstart",
    ),
]


def _latest_artifact(artifacts: list[dict[str, Any]], artifact_type: str) -> dict[str, Any] | None:
    matches = [item for item in artifacts if item.get("artifact_type") == artifact_type]
    return matches[-1] if matches else None


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[idx], 2)


async def _run_case(case: EvalCase) -> dict[str, Any]:
    result = await run_agent(
        user_message=case.user_message,
        image_paths=[case.image_path],
        trigger="eval",
    )
    run = session_manager.get_run(result["run_id"])
    artifacts = artifact_service.list_artifacts(result["run_id"])
    approvals = approval_service.list_records(result["run_id"])

    ocr_artifact = _latest_artifact(artifacts, "ocr_artifact") or {}
    retrieval_artifact = _latest_artifact(artifacts, "retrieval_hits") or {}
    decision_artifact = _latest_artifact(artifacts, "action_recommendation") or {}
    ocr_text = (ocr_artifact.get("content_json") or {}).get("text", "")
    retrieval_titles = [item["title"] for item in (retrieval_artifact.get("content_json") or {}).get("hits", [])]
    decision_payload = decision_artifact.get("content_json") or {}
    approval_required = any(item.get("status") == "required" for item in approvals)
    latency_ms = float(run.get("latency_ms") or 0.0) if run else 0.0

    return {
        "name": case.name,
        "run_id": result["run_id"],
        "expected_high_risk": case.expected_risk_level == "high" or case.expected_approval_required,
        "ocr_match": case.expected_ocr_contains.lower() in ocr_text.lower(),
        "retrieval_match": case.expected_doc_title in retrieval_titles if case.expected_doc_title else None,
        "risk_match": decision_payload.get("risk_level") == case.expected_risk_level,
        "approval_match": approval_required == case.expected_approval_required,
        "latency_ms": latency_ms,
        "retrieval_titles": retrieval_titles,
        "decision_risk_level": decision_payload.get("risk_level"),
        "approval_required": approval_required,
    }


class FailingOCRProvider:
    name = "failing_ocr"

    async def extract_text(self, frame: FrameRef):
        raise RuntimeError("forced OCR failure")


class FailingVisionProvider:
    name = "failing_vision"

    async def analyze_scene(self, frame: FrameRef, prompt: str, ocr_text: str = ""):
        raise RuntimeError("forced vision failure")


class FailingRetrievalProvider:
    name = "failing_retrieval"

    async def search(self, query: str, limit: int = 5):
        raise RuntimeError("forced retrieval failure")


class FailingDecisionProvider:
    name = "failing_decision"

    async def recommend(self, *, prompt: str, scene_summary: str, ocr_text: str, retrieved_docs: list[Any]):
        raise RuntimeError("forced decision failure")


class FailingSpeechProvider:
    name = "failing_speech"

    async def transcribe(self, audio_path: str) -> str:
        raise RuntimeError("forced speech failure")


class FailingEmbeddingProvider:
    name = "failing_embedding"

    async def embed(self, text: str) -> list[float]:
        raise RuntimeError("forced embedding failure")


async def _eval_provider_fallbacks() -> dict[str, Any]:
    handle = session_manager.start_run(
        user_id=DEMO_USER_ID,
        user_message="provider fallback eval",
        trigger="eval",
        image_count=1,
        input_payload={"image_path": str(EVAL_DIR / "warning_panel.png")},
        plan=build_default_plan(user_message="provider fallback eval", has_image=True),
    )
    frame = FrameRef(
        artifact_id=f"{handle.run_id}:frame:0",
        uri=str(EVAL_DIR / "warning_panel.png"),
        mime_type="image/png",
    )
    successes = 0
    total = 6

    ocr = await scene_pipeline_service.run_ocr(
        session_id=handle.session_id,
        run_id=handle.run_id,
        frame=frame,
        providers=[FailingOCRProvider(), LocalOCRProvider()],
        user_id=DEMO_USER_ID,
    )
    successes += int(ocr.provider == "local")

    vision = await scene_pipeline_service.run_vision(
        session_id=handle.session_id,
        run_id=handle.run_id,
        frame=frame,
        prompt="Inspect the warning panel",
        ocr_text=ocr.text,
        providers=[FailingVisionProvider(), LocalVisionProvider()],
        user_id=DEMO_USER_ID,
    )
    successes += int(vision.provider == "local")

    retrieval = await scene_pipeline_service.run_retrieval(
        session_id=handle.session_id,
        run_id=handle.run_id,
        query="warning voltage manual",
        providers=[FailingRetrievalProvider(), SQLiteRetrievalProvider()],
        user_id=DEMO_USER_ID,
    )
    successes += int(bool(retrieval))

    decision = await scene_pipeline_service.run_decision(
        session_id=handle.session_id,
        run_id=handle.run_id,
        prompt="What should I do next?",
        scene_summary=vision.summary,
        ocr_text=ocr.text,
        retrieved_docs=retrieval,
        providers=[FailingDecisionProvider(), LocalDecisionProvider()],
        user_id=DEMO_USER_ID,
    )
    successes += int(bool(decision.recommendation))

    transcript = await scene_pipeline_service.run_asr(
        session_id=handle.session_id,
        run_id=handle.run_id,
        audio_path=str(EVAL_DIR / "sample_audio.wav"),
        providers=[FailingSpeechProvider(), NoopSpeechProvider()],
        user_id=DEMO_USER_ID,
    )
    successes += int(bool(transcript))

    embedding = await scene_pipeline_service.run_embedding(
        session_id=handle.session_id,
        run_id=handle.run_id,
        text="warning voltage manual",
        providers=[FailingEmbeddingProvider(), LocalHashEmbeddingProvider()],
        user_id=DEMO_USER_ID,
    )
    successes += int(len(embedding) > 0)

    return {
        "successes": successes,
        "total": total,
        "success_rate": round(successes / total, 3),
    }


async def run_eval() -> dict[str, Any]:
    seed()
    case_results = []
    for case in CASES:
        case_results.append(await _run_case(case))

    ocr_scores = [1.0 if item["ocr_match"] else 0.0 for item in case_results]
    retrieval_scores = [1.0 if item["retrieval_match"] else 0.0 for item in case_results if item["retrieval_match"] is not None]
    high_risk_cases = [item for item in case_results if item["expected_high_risk"]]
    high_risk_misses = [
        item
        for item in high_risk_cases
        if not item["approval_required"] or item["decision_risk_level"] != "high"
    ]
    latencies = [float(item["latency_ms"]) for item in case_results]
    fallback = await _eval_provider_fallbacks()

    return {
        "scenario_metrics": {
            "ocr_accuracy": round(sum(ocr_scores) / len(ocr_scores), 3) if ocr_scores else 0.0,
            "retrieval_hit_rate": round(sum(retrieval_scores) / len(retrieval_scores), 3) if retrieval_scores else 0.0,
            "high_risk_miss_rate": round(len(high_risk_misses) / len(high_risk_cases), 3) if high_risk_cases else 0.0,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p95_latency_ms": _p95(latencies),
        },
        "provider_fallback": fallback,
        "cases": case_results,
    }


def main() -> None:
    result = asyncio.run(run_eval())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
