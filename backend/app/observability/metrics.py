from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class LatencySample:
    name: str
    duration_ms: float


class Timer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.started = 0.0

    def __enter__(self) -> "Timer":
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def sample(self) -> LatencySample:
        return LatencySample(
            name=self.name,
            duration_ms=round((time.perf_counter() - self.started) * 1000, 2),
        )
