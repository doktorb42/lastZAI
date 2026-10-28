"""Hybrid intent analysis for private and alliance chat messages."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class MessageIntent(str, Enum):
    QUESTION = "QUESTION"
    COMMAND = "COMMAND"
    REQUEST = "REQUEST"
    DIRECT_MESSAGE = "DIRECT_MESSAGE"
    SOCIAL = "SOCIAL"
    INFORMATION = "INFORMATION"
    GREETING = "GREETING"
    THANKS = "THANKS"
    REACTION = "REACTION"
    ALLIANCE_ANNOUNCEMENT = "ALLIANCE_ANNOUNCEMENT"
    COORDINATION = "COORDINATION"
    FOLLOW_UP = "FOLLOW_UP"
    ACTION_COMMAND = "ACTION_COMMAND"
    ACTION_REQUEST = "ACTION_REQUEST"
    MEMORY_STATEMENT = "MEMORY_STATEMENT"
    TEMPORARY_INFO = "TEMPORARY_INFO"
    CONFIRMATION = "CONFIRMATION"
    REJECTION = "REJECTION"
    UNKNOWN = "UNKNOWN"
    IGNORE = "IGNORE"


@dataclass(slots=True)
class MessageAnalysis:
    intent: MessageIntent
    confidence: float
    directed_at_peva: bool
    mentions_peva: bool
    mentions_self_name: bool
    requires_reply: bool
    reply_priority: str
    is_question: bool
    is_command: bool
    is_request: bool
    is_social: bool
    is_information: bool
    sender_uid: str
    sender_name: str | None
    group: str
    reason: str
    is_follow_up: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["intent"] = self.intent.value
        return data


class MessageIntentAnalyzer:
    """Use cheap rules first; optionally validate ambiguous text with local AI."""

    def __init__(
        self,
        *,
        ai: Any | None = None,
        identity_path: str | Path = "config/peva_identity.json",
        prompt_path: str | Path = "config/prompts/message_intent_system.txt",
        fallback_threshold: float = 0.70,
    ) -> None:
        self.ai = ai
        self.fallback_threshold = float(fallback_threshold)
        self.identity = _load_json(identity_path)
        aliases = self.identity.get("aliases") or [self.identity.get("name", "PEva")]
        self.aliases = tuple(
            item for item in (str(alias).strip() for alias in aliases) if item
        ) or ("PEva",)
        self.prompt_path = Path(prompt_path)

    def analyze(
        self,
        message: Mapping[str, Any] | Any,
        *,
        context: Mapping[str, Any] | None = None,
        self_uid: str | None = None,
    ) -> MessageAnalysis:
        payload = _message_mapping(message)
        context = context or {}
        text = " ".join(str(payload.get("text") or "").split()).strip()
        group = str(payload.get("group") or "custom").strip().lower()
        sender_uid = str(payload.get("sender_uid") or "")
        sender_name = _optional_text(payload.get("sender_name"))
        self_message = bool(self_uid and sender_uid == str(self_uid))
        mentions = _mentions_alias(text, self.aliases)
        mentions_self_name = bool(sender_name and _contains_name(text, sender_name))
        directed = mentions or bool(context.get("recent_reply_to_sender"))

        result = self._heuristic(
            text=text,
            group=group,
            sender_uid=sender_uid,
            sender_name=sender_name,
            directed=directed,
            mentions=mentions,
            mentions_self_name=mentions_self_name,
            self_message=self_message,
            duplicate=bool(context.get("duplicate")),
            has_prior_context=bool(context.get("has_private_context")),
        )
        if result.confidence < self.fallback_threshold and not self_message:
            result = self._llm_fallback(result, text=text)
        return _apply_private_policy(result)

    def _heuristic(
        self,
        *,
        text: str,
        group: str,
        sender_uid: str,
        sender_name: str | None,
        directed: bool,
        mentions: bool,
        mentions_self_name: bool,
        self_message: bool,
        duplicate: bool,
        has_prior_context: bool,
    ) -> MessageAnalysis:
        base = dict(
            directed_at_peva=directed,
            mentions_peva=mentions,
            mentions_self_name=mentions_self_name,
            requires_reply=directed,
            reply_priority="HIGH" if directed else "LOW",
            is_question=False,
            is_command=False,
            is_request=False,
            is_social=False,
            is_information=False,
            sender_uid=sender_uid,
            sender_name=sender_name,
            group=group,
        )
        def build(
            intent: MessageIntent,
            confidence: float,
            reason: str,
            **updates: Any,
        ) -> MessageAnalysis:
            values = dict(base)
            values.update(updates)
            return MessageAnalysis(intent, confidence, reason=reason, **values)

        lower = text.casefold()
        if self_message:
            return build(MessageIntent.IGNORE, 0.99, "Message was sent by PEva.")
        if (sender_name or "").casefold() in {"system", "last z", "lastz system"}:
            return build(MessageIntent.IGNORE, 0.99, "System message.")
        if duplicate:
            return build(MessageIntent.IGNORE, 0.99, "Duplicate source message key.")
        if not text:
            return build(MessageIntent.IGNORE, 0.99, "Empty message.")
        if _is_spam(text):
            return build(MessageIntent.IGNORE, 0.96, "Obvious repeated-character or link spam.")
        if lower in {"yes", "yeah", "yep", "yup", "sure", "ok", "okay"}:
            return build(MessageIntent.CONFIRMATION, 0.94, "Short confirmation in conversation.", is_follow_up=has_prior_context)
        if lower in {"no", "nope", "nah", "not really"}:
            return build(MessageIntent.REJECTION, 0.94, "Short rejection in conversation.", is_follow_up=has_prior_context)
        if has_prior_context and lower in {
            "why?", "where?", "when?", "me?", "ready?", "now?", "and?", "what about me?", "do it",
        }:
            return build(MessageIntent.FOLLOW_UP, 0.91, "Short follow-up uses the active private context.", is_follow_up=True, requires_reply=True)
        if _is_reaction(text):
            return build(MessageIntent.REACTION, 0.96, "Emoji-only reaction.", is_social=True, requires_reply=False)
        if lower in {"lol", "lmao", "rofl", "gg", "haha", "hehe", "xd"}:
            return build(MessageIntent.SOCIAL, 0.95, "Short casual reaction.", is_social=True, requires_reply=False)
        if re.search(r"\b(thanks|thank you|thx|ty)\b", lower):
            return build(MessageIntent.THANKS, 0.93, "Thanks expression.", is_social=True, requires_reply=directed)
        if _is_greeting(lower):
            return build(MessageIntent.GREETING, 0.92, "Greeting expression.", is_social=True, requires_reply=directed)
        if re.search(r"\b(everyone|all)\b.*\b(shield|reset|maintenance|war)\b", lower):
            return build(MessageIntent.ALLIANCE_ANNOUNCEMENT, 0.93, "Alliance-wide announcement.", is_information=True, requires_reply=False)
        if re.search(r"\b(we|everyone)\b.*\b(attack|rally|tyrant|reset|join|march)\b", lower):
            return build(MessageIntent.COORDINATION, 0.82, "Coordination language.", is_information=True, requires_reply=directed)
        if re.search(r"\b(?:my name is|call me|i am from|i'm from|i prefer)\b", lower):
            return build(MessageIntent.MEMORY_STATEMENT, 0.96, "Explicit durable player fact.", is_information=True, requires_reply=False)
        if re.search(r"\b(?:i work|night shift|tomorrow morning)\b", lower):
            return build(MessageIntent.MEMORY_STATEMENT, 0.88, "Potential schedule fact requiring review.", is_information=True, requires_reply=False)
        question = "?" in text or bool(re.match(r"^(who|what|when|where|why|how|can|are|do|does|is|will)\b", lower))
        request = bool(re.search(r"\b(please|pls|need help|help me|reinforce me|can you|could you)\b", lower))
        command = bool(re.search(r"\b(reinforce|join|come|check|send|shield|attack|teleport|wait|return)\b", lower))
        if request or command:
            game_action = bool(re.search(r"\b(reinforce|join|come|check|send|shield|attack|teleport|wait|return)\b", lower))
            intent = MessageIntent.ACTION_REQUEST if request and game_action else (
                MessageIntent.ACTION_COMMAND if game_action else (MessageIntent.REQUEST if request else MessageIntent.COMMAND)
            )
            return build(intent, 0.89, "Direct request or command language.", is_command=command, is_request=request, requires_reply=directed)
        if question:
            return build(MessageIntent.QUESTION, 0.86, "Question syntax.", is_question=True, requires_reply=directed)
        if directed and len(text) <= 24:
            return build(MessageIntent.DIRECT_MESSAGE, 0.88, "Direct PEva mention.", requires_reply=True)
        if len(text) >= 20:
            return build(MessageIntent.INFORMATION, 0.58, "Unclassified informational message.", is_information=True, requires_reply=False)
        return build(MessageIntent.UNKNOWN, 0.35, "No reliable deterministic intent.", requires_reply=False)

    def _llm_fallback(self, baseline: MessageAnalysis, *, text: str) -> MessageAnalysis:
        completer = getattr(self.ai, "complete_task_safe", None)
        if not callable(completer):
            return baseline
        prompt = _read_prompt(self.prompt_path)
        if not prompt:
            return baseline
        request = json.dumps(
            {
                "message": text,
                "group": baseline.group,
                "aliases": list(self.aliases),
                "baseline": baseline.to_dict(),
            },
            ensure_ascii=False,
        )
        try:
            reply = completer(system_prompt=prompt, user_prompt=request)
            parsed = json.loads(str(getattr(reply, "text", "") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return _unknown_from(baseline, "LLM intent fallback returned invalid JSON.")
        if not isinstance(parsed, Mapping):
            return _unknown_from(baseline, "LLM intent fallback returned non-object JSON.")
        try:
            intent = MessageIntent(str(parsed.get("intent") or "UNKNOWN").upper())
            confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        except (ValueError, TypeError):
            return _unknown_from(baseline, "LLM intent fallback used invalid fields.")
        if confidence < self.fallback_threshold:
            return _unknown_from(baseline, "LLM intent fallback had low confidence.")
        directed = bool(parsed.get("directed_at_peva", baseline.directed_at_peva))
        requires = bool(parsed.get("requires_reply", directed))
        return MessageAnalysis(
            intent=intent,
            confidence=confidence,
            directed_at_peva=directed,
            mentions_peva=baseline.mentions_peva,
            mentions_self_name=baseline.mentions_self_name,
            requires_reply=requires,
            reply_priority="HIGH" if directed else "LOW",
            is_question=intent == MessageIntent.QUESTION,
            is_command=intent in {MessageIntent.COMMAND, MessageIntent.ACTION_COMMAND},
            is_request=intent in {MessageIntent.REQUEST, MessageIntent.ACTION_REQUEST},
            is_social=intent in {MessageIntent.SOCIAL, MessageIntent.GREETING, MessageIntent.THANKS, MessageIntent.REACTION},
            is_information=intent in {MessageIntent.INFORMATION, MessageIntent.ALLIANCE_ANNOUNCEMENT, MessageIntent.COORDINATION},
            sender_uid=baseline.sender_uid,
            sender_name=baseline.sender_name,
            group=baseline.group,
            reason=str(parsed.get("reason") or "Validated by local LLM fallback."),
        )


def _apply_private_policy(analysis: MessageAnalysis) -> MessageAnalysis:
    if analysis.group != "custom":
        return analysis
    if analysis.intent == MessageIntent.IGNORE:
        return analysis
    analysis.requires_reply = True
    analysis.reply_priority = "HIGH"
    if not analysis.reason.endswith("Private-message policy requires a reply."):
        analysis.reason += " Private-message policy requires a reply."
    return analysis


def _unknown_from(baseline: MessageAnalysis, reason: str) -> MessageAnalysis:
    baseline.intent = MessageIntent.UNKNOWN
    baseline.confidence = 0.2
    baseline.requires_reply = False
    baseline.reply_priority = "LOW"
    baseline.reason = reason
    return baseline


def _message_mapping(message: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(message, Mapping):
        return dict(message)
    to_dict = getattr(message, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, Mapping):
            return dict(data)
    return {
        key: getattr(message, key, None)
        for key in ("text", "group", "sender_uid", "sender_name", "source_message_key")
    }


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, Mapping) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _read_prompt(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _contains_name(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I))


def _mentions_alias(text: str, aliases: tuple[str, ...]) -> bool:
    return any(_contains_name(text, alias) for alias in aliases)


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _is_greeting(lower: str) -> bool:
    return bool(re.search(r"\b(hi|hello|hey|morning|good morning|good evening|yo)\b", lower)) and len(lower.split()) <= 5


def _is_reaction(text: str) -> bool:
    return not bool(re.search(r"[A-Za-z0-9]", text)) and len(text) <= 16


def _is_spam(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return len(compact) >= 12 and len(set(compact.casefold())) <= 2 or compact.count("http") > 1
