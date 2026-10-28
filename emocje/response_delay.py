"""Non-blocking deterministic scheduling for human-like reply timing."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class ResponseDelay:
    due_at: float
    seconds: float
    category: str


class ResponseDelayManager:
    def __init__(self, *, enabled: bool = True, casual_range: tuple[float, float] = (1.0, 8.0),
                 normal_range: tuple[float, float] = (1.0, 5.0), urgent_range: tuple[float, float] = (0.0, 2.0),
                 rng: random.Random | None = None) -> None:
        self.enabled = bool(enabled)
        self.casual_range = _range(casual_range)
        self.normal_range = _range(normal_range)
        self.urgent_range = _range(urgent_range)
        self.rng = rng or random.Random()

    def schedule(self, *, now: float, text: str, urgent: bool = False) -> ResponseDelay:
        if not self.enabled:
            return ResponseDelay(float(now), 0.0, "disabled")
        normalized = " ".join(str(text or "").split())
        if urgent:
            category, bounds = "urgent", self.urgent_range
        elif len(normalized) <= 18 or normalized.casefold() in {"lol", "yeah", "nah", "maybe"}:
            category, bounds = "casual", self.casual_range
        else:
            category, bounds = "normal", self.normal_range
        seconds = self.rng.uniform(*bounds)
        return ResponseDelay(float(now) + seconds, seconds, category)


def _range(value: tuple[float, float]) -> tuple[float, float]:
    low, high = float(value[0]), float(value[1])
    if low < 0 or high < low:
        raise ValueError("Invalid response-delay range.")
    return low, high
