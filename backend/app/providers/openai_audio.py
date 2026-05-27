from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ..config import OPENAI_API_KEY, OPENAI_TRANSCRIBE_MODEL, OPENAI_TRANSCRIBE_TIMEOUT_SEC


class OpenAITranscriptionProvider:
    name = "openai"

    def __init__(self) -> None:
        self.model = OPENAI_TRANSCRIBE_MODEL

    async def transcribe(self, audio_path: str) -> str:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        path = Path(audio_path)
        if not path.exists():
            raise RuntimeError(f"Audio file does not exist: {audio_path}")

        mime_type = _guess_audio_mime_type(path.suffix.lower())
        timeout = httpx.Timeout(OPENAI_TRANSCRIBE_TIMEOUT_SEC)
        async with httpx.AsyncClient(timeout=timeout) as client:
            with path.open("rb") as handle:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    data={
                        "model": self.model,
                        "response_format": "json",
                    },
                    files={
                        "file": (path.name, handle, mime_type),
                    },
                )
        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI transcription failed: {response.status_code} {response.text[:240]}")

        payload = _coerce_json(response)
        text = str(payload.get("text", "")).strip()
        if not text:
            raise RuntimeError("OpenAI transcription returned an empty transcript.")
        return text


def _guess_audio_mime_type(suffix: str) -> str:
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".m4a":
        return "audio/m4a"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".webm":
        return "audio/webm"
    if suffix == ".ogg":
        return "audio/ogg"
    if suffix == ".mp4":
        return "audio/mp4"
    return "application/octet-stream"


def _coerce_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI transcription returned non-JSON payload: {response.text[:240]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OpenAI transcription returned an unexpected payload shape.")
    return payload
