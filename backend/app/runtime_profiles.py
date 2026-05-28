from __future__ import annotations

from dataclasses import dataclass

from .config import (
    ALIGNMENT_FUTURE_TOLERANCE_MS,
    ALIGNMENT_MAX_AUDIO_WINDOWS,
    ALIGNMENT_WINDOW_MS,
)

DEFAULT_CAPTURE_PROFILE = "balanced"


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    display_name: str
    summary: str
    alignment_window_ms: int
    alignment_future_tolerance_ms: int
    alignment_max_audio_windows: int

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.profile_id,
            "display_name": self.display_name,
            "summary": self.summary,
            "alignment_window_ms": self.alignment_window_ms,
            "alignment_future_tolerance_ms": self.alignment_future_tolerance_ms,
            "alignment_max_audio_windows": self.alignment_max_audio_windows,
        }


_PROFILES: dict[str, RuntimeProfile] = {
    "eco": RuntimeProfile(
        profile_id="eco",
        display_name="Eco",
        summary="Longer gaps, fewer aligned audio windows, and lower upload pressure for battery-sensitive sessions.",
        alignment_window_ms=max(ALIGNMENT_WINDOW_MS + 1500, ALIGNMENT_WINDOW_MS),
        alignment_future_tolerance_ms=max(
            ALIGNMENT_FUTURE_TOLERANCE_MS + 350,
            ALIGNMENT_FUTURE_TOLERANCE_MS,
        ),
        alignment_max_audio_windows=max(1, min(ALIGNMENT_MAX_AUDIO_WINDOWS, 2)),
    ),
    "balanced": RuntimeProfile(
        profile_id="balanced",
        display_name="Balanced",
        summary="Adaptive default with moderate capture cadence and a medium multimodal context window.",
        alignment_window_ms=ALIGNMENT_WINDOW_MS,
        alignment_future_tolerance_ms=ALIGNMENT_FUTURE_TOLERANCE_MS,
        alignment_max_audio_windows=ALIGNMENT_MAX_AUDIO_WINDOWS,
    ),
    "expert": RuntimeProfile(
        profile_id="expert",
        display_name="Expert",
        summary="Shorter alignment windows, more aggressive reuse, and denser multimodal context for realtime review.",
        alignment_window_ms=max(2200, ALIGNMENT_WINDOW_MS - 1000),
        alignment_future_tolerance_ms=max(300, ALIGNMENT_FUTURE_TOLERANCE_MS - 250),
        alignment_max_audio_windows=max(ALIGNMENT_MAX_AUDIO_WINDOWS, 4),
    ),
}


def get_runtime_profile(profile_id: str | None) -> RuntimeProfile:
    normalized = (profile_id or DEFAULT_CAPTURE_PROFILE).strip().lower()
    return _PROFILES.get(normalized, _PROFILES[DEFAULT_CAPTURE_PROFILE])


def list_runtime_profiles() -> list[RuntimeProfile]:
    return [
        _PROFILES["eco"],
        _PROFILES["balanced"],
        _PROFILES["expert"],
    ]
