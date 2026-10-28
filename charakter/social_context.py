"""Small, deterministic context models for PEva's social player layer."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class Relationship(str, Enum):
    UNKNOWN = "UNKNOWN"
    ALLY = "ALLY"
    FRIEND = "FRIEND"
    LEADER = "LEADER"
    OFFICER = "OFFICER"
    KNOWN_PLAYER = "KNOWN_PLAYER"


@dataclass(slots=True)
class PlayerProfile:
    uid: str
    name: str | None = None
    alliance: str | None = None
    server_id: int | None = None
    first_seen_at: int | None = None
    last_seen_at: int | None = None
    relationship: Relationship = Relationship.UNKNOWN
    trust_level: float = 0.5
    messages_received: int = 0
    messages_sent: int = 0
    last_private_message_at: int | None = None
    last_alliance_message_at: int | None = None
    last_reply_at: int | None = None
    notes: str | None = None


@dataclass(slots=True)
class ContextMessage:
    sender_uid: str
    sender_name: str | None
    text: str
    timestamp: float
    source_message_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_uid": self.sender_uid,
            "sender_name": self.sender_name,
            "text": self.text,
            "timestamp": self.timestamp,
            "source_message_key": self.source_message_key,
        }


@dataclass(slots=True)
class AllianceConversationContext:
    """In-memory alliance timeline; it is intentionally not a PM history."""

    max_messages: int = 40
    _messages: deque[ContextMessage] = field(init=False, repr=False)
    _seen_keys: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        self.max_messages = max(1, int(self.max_messages))
        self._messages = deque(maxlen=self.max_messages)

    def add_message(self, message: Mapping[str, Any], *, timestamp: float | None = None) -> bool:
        key = _optional_text(message.get("source_message_key"))
        if key and key in self._seen_keys:
            return False
        item = ContextMessage(
            sender_uid=str(message.get("sender_uid") or ""),
            sender_name=_optional_text(message.get("sender_name")),
            text=str(message.get("text") or "").strip(),
            timestamp=time.time() if timestamp is None else float(timestamp),
            source_message_key=key,
        )
        if not item.sender_uid or not item.text:
            return False
        if len(self._messages) == self.max_messages:
            removed = self._messages[0]
            if removed.source_message_key:
                self._seen_keys.discard(removed.source_message_key)
        self._messages.append(item)
        if key:
            self._seen_keys.add(key)
        return True

    def recent_messages(self, limit: int = 15) -> list[ContextMessage]:
        """Return only the bounded, useful tail for an AI prompt."""
        return list(self._messages)[-max(0, int(limit)):]

    def render_recent(self, limit: int = 12) -> list[str]:
        return [
            f"{item.sender_name or item.sender_uid}: {item.text}"
            for item in self.recent_messages(limit)
        ]

    def __len__(self) -> int:
        return len(self._messages)


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
