from __future__ import annotations

from pathlib import Path

from ..agent import core as agent_core


async def process_image(image_path: Path, hint: str | None = None) -> None:
    prompt = hint or "Inspect this scene and tell me what I should do next."
    await agent_core.run_agent(prompt, image_paths=[str(image_path)])


async def process_text(text_path: Path) -> None:
    text = text_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return
    await agent_core.run_agent(f"Read and summarize this text: {text}")
