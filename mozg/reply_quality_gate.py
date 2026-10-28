"""Small semantic guard for pathological model output, not a length heuristic."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReplyQualityVerdict:
    accepted: bool
    reason: str = "accepted"


class ReplyQualityGate:
    _technical = re.compile(
        r"\b(traceback|exception|stack trace|internal server error|keyerror|typeerror)\b",
        re.IGNORECASE,
    )
    _prompt_leak = re.compile(
        r"(^\s*(?:system|assistant)\s*:|<\|(?:system|assistant|user)[^>]*\|>|\b(?:system prompt|assistant prompt)\b)",
        re.IGNORECASE,
    )

    def validate(self, reply: str) -> ReplyQualityVerdict:
        text = str(reply or "")
        if not text.strip():
            return ReplyQualityVerdict(False, "empty_reply")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            return ReplyQualityVerdict(False, "control_token")
        if self._technical.search(text):
            return ReplyQualityVerdict(False, "technical_exception_text")
        if self._prompt_leak.search(text):
            return ReplyQualityVerdict(False, "prompt_leakage")
        stripped = text.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            return ReplyQualityVerdict(False, "raw_structured_output")
        return ReplyQualityVerdict(True)

    @staticmethod
    def fallback() -> str:
        return "I didn't catch that — could you repeat?"
