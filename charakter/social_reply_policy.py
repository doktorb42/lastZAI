"""Deterministic preview-only guard against socially wrong silent Agency output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from mozg.message_intent import MessageIntent


@dataclass(frozen=True, slots=True)
class SocialReplyPreviewDecision:
    should_generate_preview: bool
    reason: str


class PrivateSocialReplyPolicy:
    """Keep normal social bids conversational without weakening action safety.

    The policy never sends a message.  It only permits a *preview* text
    generation when Agency returned a silent decision for an ordinary private
    social bid.  Action requests, clear closures, reactions, and temporary
    pauses remain on the regular Agency path.
    """

    _CLOSURES = frozenset({
        "k", "kk", "ok", "okay", "got it", "got it thanks", "thanks", "thank you",
        "thx", "ty", "no problem", "np", "all good", "all good thanks",
    })
    _PAUSE = re.compile(
        r"^(?:brb|back soon|one sec(?:ond)?|give me (?:a |two )?(?:minute|minutes)|"
        r"talk later|not now|be right back)\b",
        re.IGNORECASE,
    )
    _MILD_TEASE = re.compile(r"\b(?:petty|touchy|dramatic|stubborn|quiet|smug)\b", re.IGNORECASE)
    _CONGRATULATIONS = re.compile(r"\b(?:congrats|congratulations|nice one|well done|good job)\b", re.IGNORECASE)
    _SOCIAL_INTENTS = frozenset({
        MessageIntent.GREETING.value,
        MessageIntent.SOCIAL.value,
        MessageIntent.THANKS.value,
        MessageIntent.DIRECT_MESSAGE.value,
        MessageIntent.QUESTION.value,
        MessageIntent.FOLLOW_UP.value,
        MessageIntent.CONFIRMATION.value,
        MessageIntent.REJECTION.value,
    })
    _ACTION_INTENTS = frozenset({
        MessageIntent.ACTION_COMMAND.value,
        MessageIntent.ACTION_REQUEST.value,
        MessageIntent.COMMAND.value,
        MessageIntent.REQUEST.value,
    })

    def evaluate(self, analysis: Mapping[str, Any] | Any, text: str) -> SocialReplyPreviewDecision:
        normalized = " ".join(str(text or "").split())
        lowered = re.sub(r"[^a-z0-9ąćęłńóśźż]+", " ", normalized.casefold()).strip()
        if not normalized:
            return SocialReplyPreviewDecision(False, "empty_message")
        if self._reaction_only(normalized):
            return SocialReplyPreviewDecision(False, "reaction_only")
        if lowered in self._CLOSURES:
            return SocialReplyPreviewDecision(False, "clear_conversation_close")
        if self._PAUSE.search(normalized):
            return SocialReplyPreviewDecision(False, "player_requested_pause")

        group = self._field(analysis, "group").casefold() or "custom"
        intent = self._field(analysis, "intent").upper()
        if group != "custom":
            return SocialReplyPreviewDecision(False, "not_a_private_message")
        if intent in {MessageIntent.IGNORE.value, MessageIntent.REACTION.value}:
            return SocialReplyPreviewDecision(False, "intent_is_silent")
        if intent in self._ACTION_INTENTS:
            return SocialReplyPreviewDecision(False, "action_request_stays_on_agency_path")
        if intent in self._SOCIAL_INTENTS:
            return SocialReplyPreviewDecision(True, "ordinary_private_social_bid")
        if self._MILD_TEASE.search(normalized):
            return SocialReplyPreviewDecision(True, "mild_teasing_is_a_social_bid")
        if self._CONGRATULATIONS.search(normalized):
            return SocialReplyPreviewDecision(True, "congratulations_are_a_social_bid")
        if bool(self._raw_field(analysis, "is_question")):
            return SocialReplyPreviewDecision(True, "private_question_needs_a_reply")
        return SocialReplyPreviewDecision(False, "not_a_clear_social_bid")

    @staticmethod
    def _field(analysis: Mapping[str, Any] | Any, key: str) -> str:
        value = PrivateSocialReplyPolicy._raw_field(analysis, key)
        return str(getattr(value, "value", value) or "")

    @staticmethod
    def _raw_field(analysis: Mapping[str, Any] | Any, key: str) -> Any:
        return analysis.get(key) if isinstance(analysis, Mapping) else getattr(analysis, key, None)

    @staticmethod
    def _reaction_only(text: str) -> bool:
        return not bool(re.search(r"[A-Za-z0-9ĄĆĘŁŃÓŚŹŻąćęłńóśźż]", text)) and len(text) <= 16
