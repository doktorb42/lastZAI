"""Conservative deterministic detector for possible long-term social facts.

Candidates are deliberately not persisted.  Stage 2 records them for review;
MemoryEngine can be enabled later only after the facts are confirmed useful.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_type: str
    content: str
    reason: str
    confidence: float
    expires_at: int | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            "memory_type": self.memory_type,
            "content": self.content,
            "reason": self.reason,
            "confidence": self.confidence,
            "expires_at": self.expires_at,
        }


class MemoryCandidateDetector:
    """Detect explicit, durable facts without an extra model call."""

    _rules: tuple[tuple[str, re.Pattern[str], str, float, bool], ...] = (
        ("schedule", re.compile(r"\b(?:work|night shift|can't join|cannot join|available)\b.*\b(?:tomorrow|friday|monday|tuesday|wednesday|thursday|weekend|at \d)", re.I), "schedule statement", .88, True),
        ("identity", re.compile(r"\b(?:my name is|call me|i am from|i'm from)\b", re.I), "identity statement", .98, False),
        ("preference", re.compile(r"\b(?:i prefer|i like|favorite|favourite)\b.*\b(?:troops?|riders?|fighters?|shooters?|event)\b", re.I), "stated preference", .93, False),
        ("playstyle", re.compile(r"\b(?:i main|i play|my troops are)\b", re.I), "playstyle statement", .82, False),
    )

    def detect(self, text: str) -> MemoryCandidate | None:
        cleaned = " ".join(str(text or "").split()).strip()
        if len(cleaned) < 8 or len(cleaned) > 300:
            return None
        for memory_type, pattern, reason, confidence, expires in self._rules:
            if pattern.search(cleaned):
                expires_at = int((time.time() + 36 * 3600) * 1000) if expires else None
                return MemoryCandidate(memory_type, cleaned, reason, confidence, expires_at)
        return None
