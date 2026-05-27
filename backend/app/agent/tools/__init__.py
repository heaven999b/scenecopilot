"""Tool schemas and dispatcher for SceneCopilot."""
from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from . import docs, memory, scene

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_ocr",
        "description": "Extract visible text from the current frame or use provided OCR sidecar text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "visible_text": {"type": "string"},
            },
            "required": ["image_path"],
        },
    },
    {
        "name": "describe_scene",
        "description": "Summarize what the frame likely contains and classify risk level.",
        "input_schema": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "prompt": {"type": "string"},
                "ocr_text": {"type": "string"},
            },
            "required": ["image_path", "prompt"],
        },
    },
    {
        "name": "search_documents",
        "description": "Search uploaded manuals, SOPs, and guides for relevant instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "make_decision",
        "description": "Generate the best next action based on scene understanding, OCR, and retrieved documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "scene_summary": {"type": "string"},
                "ocr_text": {"type": "string"},
            },
            "required": ["prompt", "scene_summary"],
        },
    },
    {
        "name": "save_scene_memory",
        "description": "Persist the scan result and action card for later review.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

ToolFn = Callable[..., Awaitable[dict[str, Any]]]

_DISPATCH: dict[str, ToolFn] = {
    "run_ocr": scene.run_ocr,
    "describe_scene": scene.describe_scene,
    "search_documents": docs.search_documents,
    "make_decision": scene.make_decision,
    "save_scene_memory": memory.save_scene_memory,
    "list_action_cards": memory.list_action_cards,
}


async def dispatch(name: str, tool_input: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"source": "dispatcher", "error": f"Unknown tool: {name}"}

    params = dict(tool_input)
    if ctx:
        params.update({key: value for key, value in ctx.items() if key not in params})

    signature = inspect.signature(fn)
    filtered = {key: value for key, value in params.items() if key in signature.parameters}
    return await fn(**filtered)
