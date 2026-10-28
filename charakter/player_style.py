"""Small style policy for natural, concise private-chat reply candidates."""

from __future__ import annotations

import re
from enum import Enum


class ReplyLengthMode(str, Enum):
    SHORT = "SHORT"
    NORMAL = "NORMAL"
    DETAILED = "DETAILED"


class PlayerStyle:
    def choose_length_mode(self, text: str) -> ReplyLengthMode:
        lower = str(text or "").casefold()
        if any(word in lower for word in ("how", "why", "strategy", "explain", "build", "guide")):
            return ReplyLengthMode.DETAILED
        if len(lower) <= 35 or lower in {"yes", "no", "now", "ready?"}:
            return ReplyLengthMode.SHORT
        return ReplyLengthMode.NORMAL

    def context_instruction(self, *, mode: ReplyLengthMode, relationship: str = "UNKNOWN") -> str:
        relationship_note = {
            "FRIEND": "Slightly casual, but do not force slang.",
            "LEADER": "Be direct and concrete.",
            "OFFICER": "Be direct and concrete.",
        }.get(str(relationship).upper(), "Use neutral, natural player tone.")
        sentence_limit = {
            ReplyLengthMode.SHORT: "Prefer one short sentence.",
            ReplyLengthMode.NORMAL: "Use one or two short sentences.",
            ReplyLengthMode.DETAILED: "Use at most three short sentences when detail helps.",
        }[mode]
        return f"REPLY LENGTH: {mode.value}. {sentence_limit} STYLE: {relationship_note}"

    def postprocess(self, text: str, *, mode: ReplyLengthMode) -> str:
        result = " ".join(str(text or "").split()).strip()
        # Only obvious assistant boilerplate is removed; style belongs chiefly
        # in the prompt rather than an ever-growing replacement list.
        result = re.sub(r"^(?:certainly|of course)[!,.\s]+", "", result, flags=re.I)
        result = re.sub(r"\bAs an AI[,]?\s*", "", result, flags=re.I)
        if mode != ReplyLengthMode.DETAILED:
            sentences = re.split(r"(?<=[.!?])\s+", result)
            limit = 1 if mode == ReplyLengthMode.SHORT else 2
            result = " ".join(sentences[:limit]).strip()
        return result
