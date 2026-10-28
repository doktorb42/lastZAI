"""Deterministic reply/ignore policy and anti-spam state for Stage 2."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping

from mozg.message_intent import MessageAnalysis, MessageIntent


class SocialPriority(IntEnum):
    """Priority local to Character; it has no legacy goal-loop dependency."""

    CRITICAL = 100
    HIGH = 75
    NORMAL = 50
    LOW = 25
    BACKGROUND = 0


@dataclass(slots=True)
class SocialDecision:
    should_reply: bool
    reason: str
    priority: SocialPriority = SocialPriority.LOW
    cooldown_key: str | None = None
    requires_user_confirmation: bool = False

    @property
    def action(self) -> str:
        if self.requires_user_confirmation:
            return "ask_user"
        return "reply" if self.should_reply else "ignore"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "should_reply": self.should_reply,
            "reason": self.reason,
            "priority": int(self.priority),
            "cooldown_key": self.cooldown_key,
            "requires_user_confirmation": self.requires_user_confirmation,
        }


class SocialPolicy:
    def __init__(
        self,
        *,
        player_reply_cooldown: float = 5.0,
        alliance_reply_cooldown: float = 15.0,
        global_alliance_cooldown: float = 10.0,
        now: Any = time.time,
    ) -> None:
        self.player_reply_cooldown = max(0.0, float(player_reply_cooldown))
        self.alliance_reply_cooldown = max(0.0, float(alliance_reply_cooldown))
        self.global_alliance_cooldown = max(0.0, float(global_alliance_cooldown))
        self.now = now
        self._last_player_reply: dict[str, float] = {}
        self._last_alliance_reply: dict[str, float] = {}
        self._last_global_alliance_reply: float | None = None

    def evaluate(
        self,
        analysis: MessageAnalysis,
        message: Mapping[str, Any],
        context: Any | None = None,
        world_state: Any | None = None,
        profile: Any | None = None,
    ) -> SocialDecision:
        del context, world_state, profile
        now = float(self.now())
        sender_uid = str(analysis.sender_uid or message.get("sender_uid") or "")
        group = analysis.group
        key = f"{group}:{sender_uid}"
        if analysis.intent == MessageIntent.IGNORE:
            return SocialDecision(False, analysis.reason, cooldown_key=key)
        if group == "custom":
            # Direct messages are the primary PEva conversation channel.
            # The loop batches briefly; a global cooldown must not suppress a
            # legitimate statement such as a preference or follow-up.
            return SocialDecision(True, "Private-message policy accepts non-ignored messages.", SocialPriority.HIGH, key)
        if group != "alliance":
            return SocialDecision(False, "Unsupported social channel.", cooldown_key=key)
        if analysis.intent in {MessageIntent.REACTION, MessageIntent.SOCIAL, MessageIntent.ALLIANCE_ANNOUNCEMENT, MessageIntent.IGNORE}:
            return SocialDecision(False, "Alliance message is casual or announcement content not directed at PEva.", cooldown_key=key)
        if analysis.intent == MessageIntent.GREETING and not analysis.directed_at_peva:
            return SocialDecision(False, "Unaddressed alliance greeting does not need a reply.", cooldown_key=key)
        if analysis.intent == MessageIntent.THANKS and not analysis.directed_at_peva:
            return SocialDecision(False, "Unaddressed thanks does not need an alliance reply.", cooldown_key=key)
        direct = analysis.directed_at_peva or analysis.mentions_peva
        if not direct:
            return SocialDecision(False, "Alliance message is not directed at PEva.", cooldown_key=key)
        if self._cooling(self._last_alliance_reply.get(sender_uid), now, self.alliance_reply_cooldown):
            return SocialDecision(False, "Alliance per-player reply cooldown prevents spam.", cooldown_key=key)
        if self._cooling(self._last_global_alliance_reply, now, self.global_alliance_cooldown):
            return SocialDecision(False, "Global alliance reply cooldown prevents chat spam.", cooldown_key=key)
        priority = SocialPriority.HIGH if direct else SocialPriority.NORMAL
        return SocialDecision(True, "Direct alliance mention or question is relevant to PEva.", priority, key)

    def note_reply(self, *, group: str, sender_uid: str, at: float | None = None) -> None:
        timestamp = float(self.now() if at is None else at)
        sender_uid = str(sender_uid)
        if group == "custom":
            self._last_player_reply[sender_uid] = timestamp
        elif group == "alliance":
            self._last_alliance_reply[sender_uid] = timestamp
            self._last_global_alliance_reply = timestamp

    @staticmethod
    def _cooling(last: float | None, now: float, cooldown: float) -> bool:
        return last is not None and now - last < cooldown
