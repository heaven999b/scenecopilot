from __future__ import annotations

from dataclasses import dataclass

from .config import (
    ALIGNMENT_FUTURE_TOLERANCE_MS,
    ALIGNMENT_MAX_AUDIO_WINDOWS,
    ALIGNMENT_WINDOW_MS,
    SCAN_AGGREGATION_DELAY_MS,
    SCAN_AGGREGATION_MAX_FRAMES,
    SCAN_AGGREGATION_SCENE_GAP_MS,
)

DEFAULT_CAPTURE_PROFILE = "balanced"


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    display_name: str
    summary: str
    aggregation_delay_ms: int
    aggregation_max_frames: int
    aggregation_scene_gap_ms: int
    alignment_window_ms: int
    alignment_future_tolerance_ms: int
    alignment_max_audio_windows: int

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.profile_id,
            "display_name": self.display_name,
            "summary": self.summary,
            "aggregation_delay_ms": self.aggregation_delay_ms,
            "aggregation_max_frames": self.aggregation_max_frames,
            "aggregation_scene_gap_ms": self.aggregation_scene_gap_ms,
            "alignment_window_ms": self.alignment_window_ms,
            "alignment_future_tolerance_ms": self.alignment_future_tolerance_ms,
            "alignment_max_audio_windows": self.alignment_max_audio_windows,
        }


@dataclass(frozen=True)
class AggregationPolicy:
    delay_ms: int
    max_frames: int
    scene_gap_ms: int
    load_tier: str
    pending_runs: int
    active_runs: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "delay_ms": self.delay_ms,
            "max_frames": self.max_frames,
            "scene_gap_ms": self.scene_gap_ms,
            "load_tier": self.load_tier,
            "pending_runs": self.pending_runs,
            "active_runs": self.active_runs,
        }


_PROFILES: dict[str, RuntimeProfile] = {
    "eco": RuntimeProfile(
        profile_id="eco",
        display_name="Eco",
        summary="Longer gaps, fewer aligned audio windows, and lower upload pressure for battery-sensitive sessions.",
        aggregation_delay_ms=max(SCAN_AGGREGATION_DELAY_MS + 450, SCAN_AGGREGATION_DELAY_MS),
        aggregation_max_frames=max(2, min(SCAN_AGGREGATION_MAX_FRAMES, 2)),
        aggregation_scene_gap_ms=max(SCAN_AGGREGATION_SCENE_GAP_MS + 900, SCAN_AGGREGATION_SCENE_GAP_MS),
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
        aggregation_delay_ms=SCAN_AGGREGATION_DELAY_MS,
        aggregation_max_frames=SCAN_AGGREGATION_MAX_FRAMES,
        aggregation_scene_gap_ms=SCAN_AGGREGATION_SCENE_GAP_MS,
        alignment_window_ms=ALIGNMENT_WINDOW_MS,
        alignment_future_tolerance_ms=ALIGNMENT_FUTURE_TOLERANCE_MS,
        alignment_max_audio_windows=ALIGNMENT_MAX_AUDIO_WINDOWS,
    ),
    "expert": RuntimeProfile(
        profile_id="expert",
        display_name="Expert",
        summary="Shorter alignment windows, more aggressive reuse, and denser multimodal context for realtime review.",
        aggregation_delay_ms=max(300, SCAN_AGGREGATION_DELAY_MS - 350),
        aggregation_max_frames=max(SCAN_AGGREGATION_MAX_FRAMES, 4),
        aggregation_scene_gap_ms=max(900, SCAN_AGGREGATION_SCENE_GAP_MS - 650),
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


def resolve_aggregation_policy(
    profile: RuntimeProfile,
    scheduler_snapshot: dict[str, int] | None,
) -> AggregationPolicy:
    snapshot = scheduler_snapshot or {}
    pending_runs = max(0, int(snapshot.get("pending_runs", 0)))
    active_runs = max(0, int(snapshot.get("active_runs", 0)))
    max_pending = max(1, int(snapshot.get("max_pending_runs", 1)))
    max_concurrent = max(1, int(snapshot.get("max_concurrent_runs", 1)))

    pending_ratio = pending_runs / max_pending
    active_ratio = active_runs / max_concurrent

    load_tier = "steady"
    delay_multiplier = 1.0
    frame_bonus = 0
    gap_multiplier = 1.0

    if pending_ratio >= 0.45 or active_ratio >= 0.75:
        load_tier = "elevated"
        delay_multiplier = 1.25
        frame_bonus = 1
        gap_multiplier = 1.15
    if pending_ratio >= 0.75 or active_ratio >= 0.95:
        load_tier = "congested"
        delay_multiplier = 1.55
        frame_bonus = 2
        gap_multiplier = 1.3

    return AggregationPolicy(
        delay_ms=max(250, int(round(profile.aggregation_delay_ms * delay_multiplier))),
        max_frames=max(1, min(6, profile.aggregation_max_frames + frame_bonus)),
        scene_gap_ms=max(600, int(round(profile.aggregation_scene_gap_ms * gap_multiplier))),
        load_tier=load_tier,
        pending_runs=pending_runs,
        active_runs=active_runs,
    )
