from __future__ import annotations

from pathlib import Path

from ..agent import core as agent_core


async def process_audio(audio_path: Path, session_id: str | None = None) -> None:
    prompt = "Transcribe the spoken request and tell me the safest next step."
    await agent_core.run_agent(prompt, session_id=session_id, audio_paths=[str(audio_path)])


async def process_image(image_path: Path, hint: str | None = None, session_id: str | None = None) -> None:
    prompt = hint or "Inspect this scene and tell me what I should do next."
    await agent_core.run_agent(prompt, session_id=session_id, image_paths=[str(image_path)])


async def process_combined(audio_path: Path, image_path: Path, session_id: str | None = None) -> None:
    prompt = "Use the latest voice context and scene frame together, then recommend the safest next step."
    await agent_core.run_agent(
        prompt,
        session_id=session_id,
        image_paths=[str(image_path)],
        audio_paths=[str(audio_path)],
    )


async def process_text(text_path: Path) -> None:
    text = text_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return
    await agent_core.run_agent(f"Read and summarize this text: {text}")
