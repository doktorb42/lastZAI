"""Small, local and deliberately conservative Last Z reference notes.

This is not a game wiki and it never fetches information while PEva is
running.  It only offers a few curated, dated notes when the newest message
clearly asks about the same subject.  The game screen and a player's own
context always take precedence over these notes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_KNOWLEDGE_PATH = (
    Path(__file__).resolve().parents[1] / "assets" / "knowledge" / "last_z_basics.json"
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_MAX_CONTEXT_NOTES = 2


@dataclass(frozen=True, slots=True)
class GameKnowledgeFact:
    """A dated, source-linked fact usable as model context."""

    fact_id: str
    keywords: tuple[str, ...]
    text: str
    source_url: str
    checked_on: str


class GameKnowledgeBase:
    """Load and retrieve compact, local game facts without guessing."""

    def __init__(self, facts: Iterable[GameKnowledgeFact] = ()) -> None:
        self._facts = tuple(facts)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_KNOWLEDGE_PATH) -> "GameKnowledgeBase":
        source = Path(path)
        if not source.is_file():
            return cls()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return cls()
        records = payload.get("facts", []) if isinstance(payload, dict) else []
        facts: list[GameKnowledgeFact] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                fact_id = _clean_text(record.get("id"), limit=64)
                text = _clean_text(record.get("text"), limit=360)
                source_url = _clean_text(record.get("source_url"), limit=300)
                checked_on = _clean_text(record.get("checked_on"), limit=16)
                keywords = tuple(
                    _token(item)
                    for item in record.get("keywords", [])
                    if isinstance(item, str) and _token(item)
                )
                if not keywords:
                    raise ValueError("knowledge fact needs keywords")
                facts.append(GameKnowledgeFact(fact_id, keywords, text, source_url, checked_on))
            except (TypeError, ValueError):
                continue
        return cls(facts)

    def for_message(self, message: str) -> tuple[str, ...]:
        """Return at most two relevant cautioned notes for a game question.

        Matching a word alone is intentionally insufficient: a message must
        look like a question or request for advice.  This avoids injecting
        game lore into ordinary social conversation.
        """
        normalized = _tokens(message)
        if not _looks_like_question(message, normalized):
            return ()
        scored: list[tuple[int, GameKnowledgeFact]] = []
        for fact in self._facts:
            score = sum(keyword in normalized for keyword in fact.keywords)
            if score:
                scored.append((score, fact))
        scored.sort(key=lambda item: (-item[0], item[1].fact_id))
        return tuple(
            "COMMUNITY GAME NOTE (verify in your current game): " + fact.text
            for _, fact in scored[:_MAX_CONTEXT_NOTES]
        )

    def snapshot(self) -> tuple[dict[str, str], ...]:
        """Metadata for local diagnostics; not sent to a model."""
        return tuple(
            {
                "id": fact.fact_id,
                "source_url": fact.source_url,
                "checked_on": fact.checked_on,
            }
            for fact in self._facts
        )


def _clean_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("knowledge text missing")
    compact = " ".join(value.split())
    if not compact or len(compact) > limit:
        raise ValueError("invalid knowledge text")
    return compact


def _token(value: str) -> str:
    tokens = _tokens(value)
    return next(iter(tokens), "")


def _tokens(value: str) -> set[str]:
    return set(_WORD_RE.findall(value.casefold()))


def _looks_like_question(message: str, tokens: set[str]) -> bool:
    if "?" in message:
        return True
    return bool(tokens & {"how", "what", "where", "when", "which", "should", "help", "advice", "best"})
