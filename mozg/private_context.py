"""Deterministic runtime context for one private PEva conversation."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class PrivateContextMessage:
    role: str
    text: str
    timestamp: float
    source_message_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "timestamp": self.timestamp,
            "source_message_key": self.source_message_key,
        }


@dataclass(slots=True)
class PrivateConversationContext:
    player_uid: str
    room_id: str
    max_recent_messages: int = 24
    recent_messages: deque[PrivateContextMessage] = field(init=False, repr=False)
    last_user_message_at: float | None = None
    last_peva_reply_at: float | None = None
    current_topic: str | None = None
    active_request: str | None = None
    active_command: Any | None = None
    pending_commands: list[Any] = field(default_factory=list, repr=False)
    pending_question: str | None = None
    waiting_for_followup: bool = False
    conversation_started_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    waiting_until: float | None = None

    def __post_init__(self) -> None:
        self.max_recent_messages = max(20, min(30, int(self.max_recent_messages)))
        self.recent_messages = deque(maxlen=self.max_recent_messages)

    def add_user_message(self, message: Mapping[str, Any], *, timestamp: float | None = None) -> None:
        now = time.time() if timestamp is None else float(timestamp)
        text = " ".join(str(message.get("text") or "").split()).strip()
        if not text:
            return
        self.recent_messages.append(PrivateContextMessage(
            role="user", text=text, timestamp=now,
            source_message_key=_optional_text(message.get("source_message_key")),
        ))
        self.last_user_message_at = now
        self.last_activity_at = now

    def add_peva_reply(self, text: str, *, timestamp: float | None = None) -> None:
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return
        now = time.time() if timestamp is None else float(timestamp)
        self.recent_messages.append(PrivateContextMessage("assistant", clean, now))
        self.last_peva_reply_at = now
        self.last_activity_at = now
        self.waiting_for_followup = True

    def apply_analysis(self, analysis: Any) -> None:
        intent = str(getattr(getattr(analysis, "intent", None), "value", ""))
        if bool(getattr(analysis, "is_question", False)):
            self.pending_question = intent or "QUESTION"
            self.waiting_for_followup = True
        if bool(getattr(analysis, "is_request", False)):
            self.active_request = intent or "REQUEST"
            self.waiting_for_followup = True
        if intent == "FOLLOW_UP":
            self.waiting_for_followup = True

    def set_command(self, command: Any, *, waiting_until: float | None = None) -> None:
        self.pending_commands.append(command)
        self.pending_commands = self.pending_commands[-8:]
        # Do not let a subsequent low-risk "wait" hide an earlier action
        # request that still has no confirmation.
        if self.active_command is None or _command_risk(command) >= _command_risk(self.active_command):
            self.active_command = command
        if waiting_until is not None:
            self.waiting_until = waiting_until
        self.waiting_for_followup = True
        self.last_activity_at = time.time()

    def relevant_messages(self, *, limit: int = 12, exclude_last: int = 0) -> list[PrivateContextMessage]:
        items = list(self.recent_messages)
        if exclude_last > 0:
            items = items[:-exclude_last] if len(items) > exclude_last else []
        return items[-max(0, min(15, int(limit))):]

    def render_relevant(self, *, limit: int = 12, exclude_last: int = 0) -> list[str]:
        return [
            f"{'PLAYER' if item.role == 'user' else 'PEVA'}: {item.text}"
            for item in self.relevant_messages(limit=limit, exclude_last=exclude_last)
        ]

    def snapshot(self) -> dict[str, Any]:
        command_type = getattr(getattr(self.active_command, "command_type", None), "value", None)
        return {
            "player_uid": self.player_uid,
            "room_id": self.room_id,
            "last_user_message_at": self.last_user_message_at,
            "last_peva_reply_at": self.last_peva_reply_at,
            "current_topic": self.current_topic,
            "active_request": self.active_request,
            "active_command": command_type,
            "pending_commands": [
                getattr(getattr(item, "command_type", None), "value", None)
                for item in self.pending_commands
            ],
            "pending_question": self.pending_question,
            "waiting_for_followup": self.waiting_for_followup,
            "conversation_started_at": self.conversation_started_at,
            "last_activity_at": self.last_activity_at,
            "waiting_until": self.waiting_until,
            "recent_messages": [item.to_dict() for item in self.recent_messages],
        }


class PrivateContextRegistry:
    """Runtime-only contexts keyed by the direct PM room and player."""

    def __init__(self, *, max_recent_messages: int = 24) -> None:
        self.max_recent_messages = max_recent_messages
        self._contexts: dict[tuple[str, str], PrivateConversationContext] = {}

    def get_or_create(self, *, player_uid: str, room_id: str) -> PrivateConversationContext:
        key = (str(player_uid), str(room_id))
        context = self._contexts.get(key)
        if context is None:
            context = PrivateConversationContext(
                player_uid=key[0], room_id=key[1],
                max_recent_messages=self.max_recent_messages,
            )
            self._contexts[key] = context
        return context

    def get(self, *, player_uid: str, room_id: str) -> PrivateConversationContext | None:
        return self._contexts.get((str(player_uid), str(room_id)))


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _command_risk(command: Any) -> int:
    risk = str(getattr(getattr(command, "risk_level", None), "value", "LOW")).upper()
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(risk, 1)
