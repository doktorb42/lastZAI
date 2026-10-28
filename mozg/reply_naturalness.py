"""Bounded, explainable anti-boilerplate checks for PEva replies."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Iterable


class NaturalnessVerdict(str, Enum):
    PASS = "PASS"
    REWRITE_RECOMMENDED = "REWRITE_RECOMMENDED"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class NaturalnessResult:
    verdict: NaturalnessVerdict
    reason: str
    similarity: float = 0.0


class ReplyNaturalness:
    """Detect assistant-like boilerplate and per-player repetition.

    This intentionally recommends one rewrite rather than silently replacing
    text with templates.  Safety validation remains outside this component.
    """

    _boilerplate = (
        "how can i help you", "how can i assist you", "let me know if you need anything",
        "i appreciate your concern", "sure thing how about this", "what can i do for you today",
    )

    def evaluate(self, text: str, *, recent_replies: Iterable[str] = ()) -> NaturalnessResult:
        candidate = _normalize(text)
        if not candidate:
            return NaturalnessResult(NaturalnessVerdict.REJECT, "empty_reply")
        if len(candidate) > 260:
            return NaturalnessResult(NaturalnessVerdict.REJECT, "reply_too_long")
        if any(phrase in candidate for phrase in self._boilerplate):
            return NaturalnessResult(NaturalnessVerdict.REWRITE_RECOMMENDED, "assistant_boilerplate")
        opening = " ".join(candidate.split()[:3])
        best = 0.0
        for previous in recent_replies:
            old = _normalize(previous)
            if not old:
                continue
            ratio = SequenceMatcher(None, candidate, old).ratio()
            best = max(best, ratio)
            if candidate == old or ratio >= 0.90:
                return NaturalnessResult(NaturalnessVerdict.REWRITE_RECOMMENDED, "recent_reply_repetition", ratio)
            if opening and opening == " ".join(old.split()[:3]) and ratio >= 0.68:
                return NaturalnessResult(NaturalnessVerdict.REWRITE_RECOMMENDED, "repeated_opening_pattern", ratio)
        return NaturalnessResult(NaturalnessVerdict.PASS, "natural_enough", best)


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().split()).strip(" .!?")
