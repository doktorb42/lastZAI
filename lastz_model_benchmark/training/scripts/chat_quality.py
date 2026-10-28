#!/usr/bin/env python3
"""Offline quality gates shared by the future-gated Last Z Chat V1 tools.

This module deliberately uses only the Python standard library.  It is a
deterministic *screening* layer, not a replacement for human review: a record
that passes remains unverified until the interactive reviewer approves it.
Nothing here imports the game, ADB, BlueStacks, benchmark fixtures, or model
training code.
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


MAX_NORMAL_REPLY_CHARACTERS = 950

# The Last Z client has display/layout issues with typographic apostrophes.
# Normalize the common variants to the only representation allowed in a
# player-ready completion: ASCII U+0027.
_CURLY_APOSTROPHES = frozenset({"\u2018", "\u2019", "\u201b", "\u2032", "\uff07"})
_APOSTROPHE_TRANSLATION = str.maketrans({character: "'" for character in _CURLY_APOSTROPHES})

_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[+-]?\d+(?:[.,]\d+)?(?![A-Za-z0-9])")
_URL_OR_EMAIL_RE = re.compile(
    r"(?:https?://\S+|www\.\S+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    flags=re.IGNORECASE,
)

# These patterns intentionally target *visible planning*, rather than natural
# phrases such as "I think" or "Let me know" that a player can genuinely use.
_META_REASONING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\s*/?\s*think\b", re.IGNORECASE),
    re.compile(r"^\s*(?:analysis|reasoning|thought process)\s*:", re.IGNORECASE),
    re.compile(r"\b(?:the\s+)?user\s+(?:wants|asked|is asking|needs|expects)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:i|we)\s+(?:need|needs|should|must|will)\s+(?:to\s+)?"
        r"(?:respond|reply|answer|write|generate|craft|plan|analy[sz]e|think)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\blet me\s+(?:think|plan|analy[sz]e|figure\s+out|break\s+down)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:prompt|system message|instruction(?:s)?|rubric|word count|token(?:s)?|"
        r"final answer|assistant response|chain of thought)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:as an ai|as a language model)\b", re.IGNORECASE),
    re.compile(r"^\s*(?:assistant|model)\s*:", re.IGNORECASE),
    # The instruction asks for the message a player would send, not a wrapper
    # around it.  Base chat models sometimes emit ``Sure, here's my reply:``
    # before a quoted answer; that is visible prompt-following scaffolding,
    # never a natural private-chat turn.
    re.compile(
        r"^\s*(?:(?:sure|okay|ok|alright|of\s+course)[,!\s-]+)?"
        r"(?:(?:here(?:'s|\s+is))\s+(?:my\s+)?|my\s+)(?:reply|response|answer)\s*[:,-]?",
        re.IGNORECASE,
    ),
)

# A Chat V1 target is supposed to sound like a person playing Last Z, not a
# customer-support bot.  These are deliberately pattern *families*, rather
# than a fragile list of complete bad answers.  Each category is reported
# separately so a quality report can say what regressed instead of hiding all
# failures under one generic flag.
#
# The patterns are intentionally narrow.  Ordinary phrases such as "I can
# help with that" can be natural in a game conversation, so the gate focuses
# on the unmistakably assistant-like constructions ("How can I assist you?",
# AI disclaimers, formal service language, etc.).
ASSISTANT_LIKE_FAILURE_CATEGORIES = frozenset(
    {
        "generic_assistant_tone",
        "support_agent_language",
        "self_identifies_as_program",
        "self_identifies_as_ai",
        "denies_normal_persona",
        "unnatural_followup",
        "conversation_reset",
        "excessive_formality",
        "empty_reply_when_reply_expected",
    }
)

_ASSISTANT_TONE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "generic_assistant_tone": (
        re.compile(
            r"\b(?:i(?:'m| am)|this(?:'s| is))\s+(?:just\s+)?(?:an?\s+)?"
            r"(?:ai|artificial intelligence|language model|chatbot|virtual assistant|computer program)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:i(?:'m| am)|this(?:'s| is))\s+here\s+(?:to\s+)?(?:help|assist)\b", re.IGNORECASE),
        re.compile(r"\b(?:what\s+can\s+i\s+do\s+for\s+you|how\s+can\s+i\s+be\s+of\s+(?:service|assistance))\b", re.IGNORECASE),
    ),
    "support_agent_language": (
        re.compile(
            r"\bhow\s+(?:can|may)\s+i\s+(?:help|assist)(?:\s+you)?(?:\s+(?:today|further))?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bwhat\s+(?:can|may)\s+i\s+(?:help|assist)\s+(?:you\s+)?with(?:\s+today)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bis\s+there\s+anything\s+else\s+i\s+can\s+(?:help|assist)(?:\s+you)?(?:\s+with)?\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:i(?:'d| would)\s+be\s+(?:happy|glad|pleased)\s+to\s+(?:help|assist)|"
            r"please\s+do\s+not\s+hesitate\s+to|at\s+your\s+service)\b",
            re.IGNORECASE,
        ),
    ),
    "self_identifies_as_program": (
        re.compile(
            r"\b(?:i(?:'m| am)|this(?:'s| is))\s+(?:just\s+)?(?:a\s+)?(?:computer\s+)?program(?:me)?\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:i(?:'m| am)|this(?:'s| is))\s+(?:just\s+)?code\b", re.IGNORECASE),
    ),
    "self_identifies_as_ai": (
        re.compile(r"\bas\s+(?:an?\s+)?ai\b", re.IGNORECASE),
        re.compile(r"\bas\s+a\s+language\s+model\b", re.IGNORECASE),
        re.compile(
            r"\b(?:i(?:'m| am)|this(?:'s| is))\s+(?:just\s+)?(?:an?\s+)?"
            r"(?:ai|artificial intelligence|language model|chatbot|virtual assistant)\b",
            re.IGNORECASE,
        ),
    ),
    "denies_normal_persona": (
        re.compile(
            r"\bi\s+(?:do\s+not|don't|cannot|can't)\s+(?:have|feel)\s+(?:any\s+)?"
            r"(?:feelings?|emotions?|personal\s+experiences?)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bi(?:'m| am)\s+(?:unable|not\s+able)\s+to\s+feel\b", re.IGNORECASE),
        re.compile(r"\bi\s+(?:do\s+not|don't)\s+have\s+(?:a\s+)?body\b", re.IGNORECASE),
    ),
    "unnatural_followup": (
        re.compile(
            r"\b(?:how\s+(?:can|may)\s+i|what\s+(?:else\s+)?can\s+i)\s+(?:help|assist)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bis\s+there\s+anything\s+else\s+i\s+can\s+(?:help|assist)\b", re.IGNORECASE),
    ),
    "excessive_formality": (
        re.compile(r"^\s*certainly[!.,]?\s*$", re.IGNORECASE),
        re.compile(
            r"\b(?:certainly|of\s+course)[!,.]?\s+(?:how\s+(?:can|may)\s+i|what\s+can\s+i|"
            r"i(?:'d| would)\s+be\s+(?:happy|pleased))\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:please\s+do\s+not\s+hesitate|it\s+would\s+be\s+my\s+pleasure|kindly)\b", re.IGNORECASE),
    ),
}

# A reset is contextual: "hey" can be a perfectly good answer to a first
# greeting, whereas reintroducing oneself after an established exchange is a
# regression.  The helper below only applies these patterns if it sees a prior
# outgoing PEva message in the supplied conversation context.
_CONVERSATION_RESET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bnice\s+to\s+meet\s+you\b", re.IGNORECASE),
    re.compile(r"\blet\s+me\s+introduce\s+myself\b", re.IGNORECASE),
    re.compile(r"\b(?:my\s+name\s+is|i(?:'m| am))\s+(?:peva|your\s+(?:ai|assistant))\b", re.IGNORECASE),
    re.compile(r"\b(?:hello|hi)\s+and\s+welcome\b", re.IGNORECASE),
)

# This is a risk score, not a replacement for the hard gate.  A non-zero
# category is enough to fail the tone gate; the score makes reports sortable.
_ASSISTANT_TONE_WEIGHTS = {
    "generic_assistant_tone": 35,
    "support_agent_language": 45,
    "self_identifies_as_program": 70,
    "self_identifies_as_ai": 70,
    "denies_normal_persona": 55,
    "unnatural_followup": 35,
    "conversation_reset": 40,
    "excessive_formality": 25,
}

# This is purposefully broad enough for concise conversational English, while
# avoiding a dependency on a language-identification package in the offline
# review flow.  A failure is a safe rejection; human reviewers can edit it.
_ENGLISH_MARKERS = frozenset(
    {
        "a",
        "about",
        "absolutely",
        "all",
        "alright",
        "and",
        "any",
        "are",
        "around",
        "awesome",
        "be",
        "because",
        "been",
        "but",
        "by",
        "can",
        "care",
        "catch",
        "chat",
        "come",
        "could",
        "definitely",
        "do",
        "don't",
        "down",
        "enjoy",
        "everyone",
        "feel",
        "for",
        "from",
        "fun",
        "get",
        "glad",
        "go",
        "good",
        "great",
        "happy",
        "have",
        "he",
        "hello",
        "help",
        "hey",
        "hi",
        "how",
        "i",
        "i'm",
        "if",
        "in",
        "is",
        "it",
        "it's",
        "just",
        "keep",
        "know",
        "later",
        "let's",
        "like",
        "little",
        "look",
        "love",
        "me",
        "message",
        "more",
        "my",
        "need",
        "nice",
        "no",
        "not",
        "now",
        "of",
        "okay",
        "on",
        "one",
        "or",
        "our",
        "out",
        "people",
        "please",
        "probably",
        "really",
        "right",
        "same",
        "see",
        "send",
        "should",
        "so",
        "some",
        "sounds",
        "sure",
        "talk",
        "tell",
        "thank",
        "thanks",
        "that",
        "the",
        "them",
        "then",
        "there",
        "they",
        "this",
        "time",
        "to",
        "today",
        "tomorrow",
        "too",
        "up",
        "want",
        "was",
        "we",
        "well",
        "what",
        "when",
        "with",
        "would",
        "yeah",
        "yes",
        "yep",
        "yo",
        "you",
        "you're",
        "your",
        "haha",
        "lol",
        "xd",
    }
)

_DISPLAY_INPUT_FIELDS = (
    "input",
    "prompt",
    "user_message",
    "conversation",
    "messages",
    "context",
    "chat_context",
)
_EXPECTED_FIELDS = (
    "expected",
    "expected_response",
    "reference",
    "reference_response",
    "target",
    "gold_response",
)
_CANDIDATE_FIELDS = (
    "candidate_text",
    "response",
    "completion",
    "output",
    "text",
    "assistant_response",
)
_SOURCE_FIELDS = ("source", "source_id", "provenance", "origin", "dataset_source")
_SCENARIO_FIELDS = ("scenario", "scenario_id", "scenario_family", "category", "case_id")


def _first_present(record: Mapping[str, Any], fields: Iterable[str]) -> Any:
    for field in fields:
        value = record.get(field)
        if value is not None and value != "":
            return value
    return None


def display_value(value: Any) -> str:
    """Return a stable human-readable representation without losing objects."""

    if value is None:
        return "<none>"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def extract_chat_input(record: Mapping[str, Any]) -> Any:
    return _first_present(record, _DISPLAY_INPUT_FIELDS)


def extract_expected(record: Mapping[str, Any]) -> Any:
    return _first_present(record, _EXPECTED_FIELDS)


def extract_candidate_text(record: Mapping[str, Any]) -> str | None:
    value = _first_present(record, _CANDIDATE_FIELDS)
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        nested = value.get("content") or value.get("text")
        return nested if isinstance(nested, str) else None
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            if isinstance(item, str):
                fragments.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                fragments.append(str(item["text"]))
        return "".join(fragments) if fragments else None
    return None


def extract_source(record: Mapping[str, Any]) -> Any:
    return _first_present(record, _SOURCE_FIELDS)


def extract_scenario(record: Mapping[str, Any]) -> Any:
    return _first_present(record, _SCENARIO_FIELDS)


def contains_curly_apostrophe(text: str) -> bool:
    return any(character in _CURLY_APOSTROPHES for character in text)


def normalize_straight_apostrophes(text: str) -> tuple[str, bool]:
    """Normalize compatibility apostrophes and say whether a change occurred."""

    normalized = text.translate(_APOSTROPHE_TRANSLATION)
    return normalized, normalized != text


def _mapping_message_text(item: Any) -> str:
    """Extract visible text from a compact history item without guessing IDs."""

    if isinstance(item, str):
        return item
    if not isinstance(item, Mapping):
        return ""
    for field in ("text", "content", "message", "body"):
        value = item.get(field)
        if isinstance(value, str):
            return value
    return ""


def _mapping_message_direction(item: Any) -> str:
    if not isinstance(item, Mapping):
        return ""
    for field in ("direction", "role", "speaker", "sender"):
        value = item.get(field)
        if isinstance(value, str):
            return value.casefold().strip()
    return ""


def _canonical_context_from_record(source_record: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Find the V2 context when a caller supplied a review/export wrapper.

    The quality gate intentionally supports only local in-memory objects.  It
    never follows a path or reads a database; callers are responsible for
    providing the already-sanitized model-facing context.
    """

    if not isinstance(source_record, Mapping):
        return None
    candidates: list[Any] = [source_record]
    for field in ("task_input", "canonical_input", "chat_input", "input", "context", "chat_context"):
        if field in source_record:
            candidates.append(source_record[field])
    task_snapshot = source_record.get("task_snapshot")
    if isinstance(task_snapshot, Mapping):
        candidates.append(task_snapshot.get("input"))
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        nested = candidate.get("context")
        if isinstance(nested, Mapping) and "incoming_message" in nested:
            return nested
        if "incoming_message" in candidate or "recent_messages" in candidate:
            return candidate
    return None


def extract_conversation_history(source_record: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Return a small normalized view of visible conversation history.

    This helper understands the canonical V2 ``recent_messages`` shape and a
    few reviewer/benchmark-friendly ``history`` aliases.  It deliberately
    returns no routing or database identifiers.
    """

    if not isinstance(source_record, Mapping):
        return []
    raw_history: Any = None
    for field in ("conversation_history", "history", "recent_messages"):
        value = source_record.get(field)
        if isinstance(value, list):
            raw_history = value
            break
    if raw_history is None:
        context = _canonical_context_from_record(source_record)
        if isinstance(context, Mapping):
            value = context.get("recent_messages")
            if isinstance(value, list):
                raw_history = value
    if not isinstance(raw_history, list):
        return []
    history: list[dict[str, str]] = []
    for item in raw_history:
        text = _mapping_message_text(item).strip()
        if not text:
            continue
        direction = _mapping_message_direction(item)
        if direction in {"outgoing", "assistant", "peva", "bot"}:
            role = "assistant"
        elif direction in {"incoming", "user", "player", "human"}:
            role = "player"
        else:
            role = "unknown"
        history.append({"role": role, "text": text})
    return history


def _current_player_message(source_record: Mapping[str, Any] | None) -> str:
    if not isinstance(source_record, Mapping):
        return ""
    for field in ("current_player_message", "incoming_message", "user_message"):
        value = source_record.get(field)
        text = _mapping_message_text(value)
        if text:
            return text.strip()
        if isinstance(value, str) and value.strip():
            return value.strip()
    context = _canonical_context_from_record(source_record)
    if isinstance(context, Mapping):
        text = _mapping_message_text(context.get("incoming_message"))
        if text:
            return text.strip()
    return ""


def infer_reply_expected(
    source_record: Mapping[str, Any] | None = None,
    *,
    reply_expected: bool | None = None,
) -> bool:
    """Resolve whether a fixture/context explicitly expects a player reply.

    A normal Chat V1 target always expects a reply, hence the safe default is
    ``True``.  Evaluation callers can explicitly set ``reply_expected=False``
    for future no-reply situations without teaching the dataset pipeline that
    empty targets are normal.
    """

    if isinstance(reply_expected, bool):
        return reply_expected
    if not isinstance(source_record, Mapping):
        return True
    candidates: list[Mapping[str, Any]] = [source_record]
    context = _canonical_context_from_record(source_record)
    if isinstance(context, Mapping):
        candidates.append(context)
    for candidate in candidates:
        for field in ("reply_expected", "response_expected", "expect_reply", "requires_reply"):
            value = candidate.get(field)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                folded = value.strip().casefold()
                if folded in {"true", "yes", "required", "expected"}:
                    return True
                if folded in {"false", "no", "optional", "not_expected"}:
                    return False
    return True


def infer_reply_word_limit(source_record: Mapping[str, Any] | None = None) -> int | None:
    """Read an explicit evaluation-only reply-length expectation, if present.

    The canonical production contract has a character limit, not a universal
    word target.  This hook is therefore opt-in: a short-social benchmark can
    set ``short_social_turn=true`` or ``max_reply_words`` without forcing the
    same brevity on a legitimate coordination conversation.
    """

    if not isinstance(source_record, Mapping):
        return None
    candidates: list[Mapping[str, Any]] = [source_record]
    expectations = source_record.get("quality_expectations")
    if isinstance(expectations, Mapping):
        candidates.append(expectations)
    for candidate in candidates:
        for field in ("max_reply_words", "max_response_words", "short_social_reply_max_words"):
            value = candidate.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        if candidate.get("short_social_turn") is True:
            return 35
    return None


def _is_simple_social_greeting(text: str) -> bool:
    folded = " ".join(text.casefold().split())
    return bool(
        re.fullmatch(
            r"(?:hi|hello|hey|yo|sup|what'?s\s+up|howdy)[!?. ,]*",
            folded,
        )
    )


def assess_multi_turn_continuity(
    candidate_text: str,
    source_record: Mapping[str, Any] | None = None,
    *,
    conversation_history: Iterable[Any] | None = None,
    current_player_message: str | None = None,
    known_tone_categories: Iterable[str] = (),
) -> dict[str, Any]:
    """Detect clear re-introductions/resets after an established exchange.

    This is intentionally conservative.  It does not call a second greeting a
    reset merely because it starts with "hey"; it requires prior outgoing PEva
    context *and* a visible reintroduction or generic service-style restart.
    """

    if conversation_history is None:
        history = extract_conversation_history(source_record)
    else:
        history = []
        for item in conversation_history:
            text = _mapping_message_text(item).strip()
            if not text:
                continue
            direction = _mapping_message_direction(item)
            role = "assistant" if direction in {"outgoing", "assistant", "peva", "bot"} else (
                "player" if direction in {"incoming", "user", "player", "human"} else "unknown"
            )
            history.append({"role": role, "text": text})
    current = current_player_message if isinstance(current_player_message, str) else _current_player_message(source_record)
    normalized, _ = normalize_straight_apostrophes(unicodedata.normalize("NFKC", candidate_text).strip())
    has_prior_peva_turn = any(item["role"] == "assistant" for item in history)
    reset_patterns = [pattern.pattern for pattern in _CONVERSATION_RESET_PATTERNS if pattern.search(normalized)]
    categories = {str(category) for category in known_tone_categories}
    # After PEva has already participated in an exchange, slipping into a
    # help-desk/AI persona is itself a conversation reset.  A reset is not
    # limited to a literal second "hello": e.g. after "i'm fine", "Great to
    # hear that! How can I assist you further?" abandons the social thread in
    # exactly the same way.  The current-message guard below intentionally
    # keeps an isolated first-turn greeting from being labelled a reset.
    assistant_like_restart = bool(
        {
            "generic_assistant_tone",
            "support_agent_language",
            "self_identifies_as_program",
            "self_identifies_as_ai",
            "denies_normal_persona",
            "unnatural_followup",
        }
        & categories
    )
    is_reset = bool(
        has_prior_peva_turn
        and not _is_simple_social_greeting(current)
        and (reset_patterns or assistant_like_restart)
    )
    return {
        "has_prior_peva_turn": has_prior_peva_turn,
        "current_player_message": current,
        "current_message_is_simple_greeting": _is_simple_social_greeting(current),
        "conversation_reset": is_reset,
        "conversation_reset_pattern_count": len(reset_patterns),
        "conversation_reset_patterns": reset_patterns,
    }


def assess_assistant_like_tone(
    value: Any,
    source_record: Mapping[str, Any] | None = None,
    *,
    conversation_history: Iterable[Any] | None = None,
    current_player_message: str | None = None,
) -> dict[str, Any]:
    """Score and hard-gate visible generic-assistant language.

    ``assistant_like_tone_score`` is a *risk* score from 0 (no detected
    assistant-like language) to 100 (strongly assistant-like).  Any detected
    category fails the tone gate even when the score is low: this keeps an
    otherwise grammatical "Certainly!" out of a Chat V1 target.
    """

    if not isinstance(value, str):
        return {
            "assistant_like_tone_score": 0,
            "assistant_like_tone_pass": True,
            "assistant_like_tone_categories": [],
            "assistant_like_pattern_counts": {},
            "continuity": assess_multi_turn_continuity("", source_record),
        }
    text, _ = normalize_straight_apostrophes(unicodedata.normalize("NFKC", value).strip())
    categories: list[str] = []
    pattern_counts: dict[str, int] = {}
    for category, patterns in _ASSISTANT_TONE_PATTERNS.items():
        count = sum(1 for pattern in patterns if pattern.search(text))
        if count:
            categories.append(category)
            pattern_counts[category] = count

    # A generic support sentence should be visible in both the broad and the
    # actionable report bucket.  This makes the primary failure easy to see
    # while still preserving the precise cause.
    category_set = set(categories)
    if category_set & {"support_agent_language", "self_identifies_as_program", "self_identifies_as_ai"}:
        category_set.add("generic_assistant_tone")
    if "support_agent_language" in category_set:
        category_set.add("unnatural_followup")

    continuity = assess_multi_turn_continuity(
        text,
        source_record,
        conversation_history=conversation_history,
        current_player_message=current_player_message,
        known_tone_categories=category_set,
    )
    if continuity["conversation_reset"]:
        category_set.add("conversation_reset")
        pattern_counts["conversation_reset"] = max(1, int(continuity["conversation_reset_pattern_count"]))

    ordered_categories = sorted(category_set)
    risk = min(100, sum(_ASSISTANT_TONE_WEIGHTS.get(category, 25) for category in ordered_categories))
    return {
        "assistant_like_tone_score": risk,
        "assistant_like_tone_pass": not ordered_categories,
        "assistant_like_tone_categories": ordered_categories,
        "assistant_like_pattern_counts": {key: pattern_counts[key] for key in sorted(pattern_counts)},
        "continuity": continuity,
    }


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        flattened: list[str] = []
        for nested in value.values():
            flattened.extend(_flatten_text(nested))
        return flattened
    if isinstance(value, Iterable):
        flattened = []
        for nested in value:
            flattened.extend(_flatten_text(nested))
        return flattened
    return [str(value)]


def grounding_text(record: Mapping[str, Any] | None) -> str:
    """Gather only prompt/context and explicit factual grounding fields.

    Expected/reference answers are deliberately excluded: they are not evidence
    for a teacher candidate and using them would invite accidental copying.
    """

    if not isinstance(record, Mapping):
        return ""
    values: list[Any] = [extract_chat_input(record)]
    for field in (
        "allowed_facts",
        "facts",
        "grounding",
        "grounded_facts",
        "known_facts",
        "world_facts",
        "memory",
        "conversation_memory",
    ):
        if field in record:
            values.append(record[field])
    return "\n".join(fragment for value in values for fragment in _flatten_text(value))


def _find_meta_reasoning(text: str) -> list[str]:
    labels: list[str] = []
    for pattern in _META_REASONING_PATTERNS:
        if pattern.search(text):
            labels.append(pattern.pattern)
    return labels


def _looks_english(text: str) -> tuple[bool, dict[str, Any]]:
    words = [word.casefold() for word in _WORD_RE.findall(text)]
    letters = [character for character in text if character.isalpha()]
    ascii_letters = [character for character in letters if character.isascii()]
    marker_count = sum(word in _ENGLISH_MARKERS for word in words)
    ascii_ratio = len(ascii_letters) / len(letters) if letters else 0.0
    # A short response such as "Thanks!" is valid English.  A candidate needs
    # at least one common English marker and predominantly Latin/ASCII letters.
    valid = bool(words) and marker_count >= 1 and ascii_ratio >= 0.80
    return valid, {
        "word_count": len(words),
        "english_marker_count": marker_count,
        "ascii_letter_ratio": round(ascii_ratio, 4),
    }


def _sentence_initial_word_positions(text: str) -> set[tuple[int, int]]:
    positions: set[tuple[int, int]] = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        match = re.search(r"\b[A-Za-z][A-Za-z'-]*\b", sentence)
        if match:
            positions.add((match.start(), match.end()))
    # Positions above are relative to fragments, so callers do not use them.
    # This helper remains intentionally unused in favor of a simpler
    # sentence-aware pattern below.
    return positions


def _proper_names_not_in_grounding(text: str, evidence: str) -> list[str]:
    """Find strong, inspectable unsupported-name signals.

    We ignore the first word of each sentence because normal capitalization is
    not a factual claim.  This is intentionally conservative; reviewers still
    see every candidate and can reject subtle hallucinations.
    """

    evidence_folded = unicodedata.normalize("NFKC", evidence).casefold()
    unsupported: list[str] = []
    sentence_start = True
    for token_match in re.finditer(r"[A-Za-z][A-Za-z'-]*", text):
        token = token_match.group(0)
        before = text[: token_match.start()]
        if token_match.start() == 0 or re.search(r"(?:[.!?]\s*|\n\s*)$", before):
            sentence_start = True
        else:
            sentence_start = False
        if sentence_start or token in {"I", "I'm", "I'll", "I'd", "I've"}:
            continue
        if not token[0].isupper() or token.casefold() in _ENGLISH_MARKERS:
            continue
        if token.casefold() not in evidence_folded:
            unsupported.append(token)
    return list(dict.fromkeys(unsupported))


def _detect_unsupported_claims(text: str, record: Mapping[str, Any] | None) -> list[str]:
    """Reject factual additions that can be verified without an LLM.

    This deliberately does not claim to prove semantic truth.  It catches
    visible forbidden phrases, new numbers/URLs and new proper names.  The
    human-review tool remains mandatory for anything subtler.
    """

    if not isinstance(record, Mapping):
        return []
    evidence = grounding_text(record)
    evidence_folded = unicodedata.normalize("NFKC", evidence).casefold()
    violations: list[str] = []

    forbidden = record.get("forbidden_claims")
    for phrase in _flatten_text(forbidden):
        normalized_phrase = " ".join(phrase.casefold().split())
        if normalized_phrase and normalized_phrase in " ".join(text.casefold().split()):
            violations.append("forbidden_claim:" + normalized_phrase[:80])

    for number in _NUMBER_RE.findall(text):
        if number.casefold() not in evidence_folded:
            violations.append("unsupported_number:" + number)

    for address in _URL_OR_EMAIL_RE.findall(text):
        if address.casefold() not in evidence_folded:
            violations.append("unsupported_contact_or_url:" + address[:80])

    for name in _proper_names_not_in_grounding(text, evidence):
        violations.append("unsupported_proper_name:" + name)

    # Explicitly factual first-person details are unsafe to invent when source
    # data did not supply their value.  Generic social phrases ("I'm glad") do
    # not match this expression.
    factual_self_claim = re.search(
        r"\b(?:i\s+(?:live|work|study)\s+(?:in|at|for)\s+\S+|"
        r"i(?:'m|\s+am)\s+(?:from|a|an)\s+\S+|my\s+(?:name|age|job|city|country|birthday)\s+is\s+\S+)",
        text,
        flags=re.IGNORECASE,
    )
    if factual_self_claim and factual_self_claim.group(0).casefold() not in evidence_folded:
        violations.append("unsupported_personal_fact")
    return list(dict.fromkeys(violations))


def validate_player_ready_output(
    value: Any,
    source_record: Mapping[str, Any] | None = None,
    *,
    apostrophe_policy: str = "normalize",
    reply_expected: bool | None = None,
    conversation_history: Iterable[Any] | None = None,
    current_player_message: str | None = None,
) -> dict[str, Any]:
    """Validate a candidate as a Last Z player-ready chat reply.

    ``apostrophe_policy`` is either ``normalize`` (default) or ``reject``.
    The returned ``normalized_text`` is always safe to send to the game's text
    field if ``valid`` is true; it has no curly apostrophes and is <=950 chars.

    Chat V1 examples use the safe default ``reply_expected=True``.  An
    evaluator can opt into ``reply_expected=False`` for a future no-reply
    situation; that permits an empty text *only* in that explicit context and
    never makes an empty training target eligible by accident.
    """

    if apostrophe_policy not in {"normalize", "reject"}:
        raise ValueError("apostrophe_policy must be 'normalize' or 'reject'")

    errors: list[str] = []
    warnings: list[str] = []
    expects_reply = infer_reply_expected(source_record, reply_expected=reply_expected)
    raw_text = value if isinstance(value, str) else ""
    # ``None`` is the representation a generation evaluator may use for an
    # explicitly allowed no-reply.  It is never accepted when a reply is due.
    if not isinstance(value, str) and not (value is None and not expects_reply):
        errors.append("candidate_text_not_a_string")

    text = unicodedata.normalize("NFKC", raw_text).strip()
    normalized_text, apostrophes_changed = normalize_straight_apostrophes(text)
    if apostrophes_changed:
        if apostrophe_policy == "reject":
            errors.append("curly_apostrophe_not_allowed")
        else:
            warnings.append("curly_apostrophes_normalized_to_ascii")

    empty_reply_allowed = not normalized_text and not expects_reply
    if not normalized_text:
        if expects_reply:
            # Keep the old generic code for compatibility while exposing a
            # reportable Chat V1 failure category with unambiguous semantics.
            errors.extend(("empty_candidate", "empty_reply_when_reply_expected"))
        else:
            warnings.append("empty_reply_allowed_by_explicit_context")

    meta_matches: list[str] = []
    unsupported_claims: list[str] = []
    reply_word_count = len(_WORD_RE.findall(normalized_text))
    reply_word_limit = infer_reply_word_limit(source_record)
    tone = assess_assistant_like_tone(
        normalized_text,
        source_record,
        conversation_history=conversation_history,
        current_player_message=current_player_message,
    )
    looks_english, english_details = _looks_english(normalized_text)
    if normalized_text:
        if len(normalized_text) > MAX_NORMAL_REPLY_CHARACTERS:
            errors.append(f"reply_exceeds_{MAX_NORMAL_REPLY_CHARACTERS}_characters")
        if reply_word_limit is not None and reply_word_count > reply_word_limit:
            errors.append("overlong_reply_for_short_social_turn")
        if any(ord(character) < 32 and character not in "\n\r\t" for character in normalized_text):
            errors.append("contains_control_character")
        if normalized_text.lstrip().startswith(("{", "[", "```")):
            errors.append("structured_or_markdown_output_not_player_ready")

        meta_matches = _find_meta_reasoning(normalized_text)
        if meta_matches:
            errors.append("visible_meta_reasoning_or_prompt_analysis")
        if not looks_english:
            errors.append("english_language_check_failed")

        unsupported_claims = _detect_unsupported_claims(normalized_text, source_record)
        errors.extend(unsupported_claims)

        # This is a hard Chat V1 gate: grammatically correct customer-service
        # language is still not a player-ready Last Z reply.
        errors.extend(tone["assistant_like_tone_categories"])

    unique_errors = list(dict.fromkeys(errors))
    unique_warnings = list(dict.fromkeys(warnings))
    valid = not unique_errors
    return {
        "valid": valid,
        "quality_pass": valid,
        "player_ready_pass": valid,
        # This deliberately drops to zero for visible reasoning and all other
        # hard failures.  ``assistant_like_tone_score`` remains a separate
        # risk score where 0 means no detected assistant-like tone.
        "quality_score": 100 if valid else 0,
        "normalized_text": normalized_text,
        "character_count": len(normalized_text),
        "word_count": reply_word_count,
        "reply_word_limit": reply_word_limit,
        "errors": unique_errors,
        "warnings": unique_warnings,
        "failure_categories": [
            category for category in unique_errors if category in ASSISTANT_LIKE_FAILURE_CATEGORIES
        ],
        "meta_reasoning_detected": bool(meta_matches),
        "meta_reasoning_pattern_count": len(meta_matches),
        "english_check": english_details,
        "unsupported_claims": unsupported_claims,
        "apostrophes_normalized": apostrophes_changed,
        "reply_expected": expects_reply,
        "empty_reply_allowed": empty_reply_allowed,
        "assistant_like_tone_score": tone["assistant_like_tone_score"],
        "assistant_like_tone_pass": tone["assistant_like_tone_pass"],
        "assistant_like_tone_categories": tone["assistant_like_tone_categories"],
        "assistant_like_pattern_counts": tone["assistant_like_pattern_counts"],
        "continuity": tone["continuity"],
        "max_characters": MAX_NORMAL_REPLY_CHARACTERS,
    }


def summarize_chat_quality_verdicts(verdicts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build stable counters for a Chat V1 quality/benchmark report.

    The caller owns persistence and report formatting; this small pure helper
    keeps the failure taxonomy identical across review, dataset validation and
    benchmark code.
    """

    total = 0
    quality_passes = 0
    tone_failures = 0
    meta_reasoning_failures = 0
    empty_reply_failures = 0
    category_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    for verdict in verdicts:
        if not isinstance(verdict, Mapping):
            continue
        total += 1
        if verdict.get("quality_pass") is True or verdict.get("valid") is True:
            quality_passes += 1
        if verdict.get("assistant_like_tone_pass") is False:
            tone_failures += 1
        if verdict.get("meta_reasoning_detected") is True:
            meta_reasoning_failures += 1
        errors = verdict.get("errors")
        if isinstance(errors, Iterable) and not isinstance(errors, (str, bytes, Mapping)):
            for error in errors:
                if not isinstance(error, str):
                    continue
                error_counts[error] = error_counts.get(error, 0) + 1
                if error == "empty_reply_when_reply_expected":
                    empty_reply_failures += 1
        categories = verdict.get("assistant_like_tone_categories")
        if isinstance(categories, Iterable) and not isinstance(categories, (str, bytes, Mapping)):
            for category in categories:
                if isinstance(category, str):
                    category_counts[category] = category_counts.get(category, 0) + 1
    return {
        "total": total,
        "quality_pass_count": quality_passes,
        "quality_fail_count": total - quality_passes,
        "quality_pass_rate": (quality_passes / total) if total else None,
        "assistant_like_tone_failure_count": tone_failures,
        "assistant_like_tone_category_counts": dict(sorted(category_counts.items())),
        "meta_reasoning_failure_count": meta_reasoning_failures,
        "empty_reply_when_reply_expected_count": empty_reply_failures,
        "error_counts": dict(sorted(error_counts.items())),
    }


def combined_warnings(record: Mapping[str, Any]) -> list[str]:
    """Collect source/generation warnings plus a fresh deterministic verdict."""

    warnings: list[str] = []
    for field in ("warnings", "quality_warnings", "validation_warnings", "validation_errors"):
        value = record.get(field)
        if isinstance(value, str):
            warnings.append(value)
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
            warnings.extend(str(item) for item in value)
    quality = record.get("quality")
    if isinstance(quality, Mapping):
        for key in ("warnings", "errors"):
            nested = quality.get(key)
            if isinstance(nested, Iterable) and not isinstance(nested, (str, bytes, Mapping)):
                warnings.extend(str(item) for item in nested)
    candidate = extract_candidate_text(record)
    if candidate is None:
        warnings.append("candidate_text_missing")
    else:
        verdict = validate_player_ready_output(candidate, record)
        warnings.extend(verdict["warnings"])
        warnings.extend(verdict["errors"])
    return list(dict.fromkeys(warnings))


def copy_with_candidate(record: Mapping[str, Any], candidate_text: str) -> dict[str, Any]:
    """Return a deep-copied record with one canonical candidate-text field."""

    copied = copy.deepcopy(dict(record))
    copied["candidate_text"] = candidate_text
    for field in ("response", "completion", "output", "text", "assistant_response"):
        if field in copied and field != "candidate_text":
            # Keep original source fields only when they were not a candidate.
            # Removing ambiguity prevents a later formatter selecting stale text.
            copied.pop(field, None)
    return copied
