"""Lightweight, serialisable events used by the PEva v2 player loop."""

from __future__ import annotations

import dataclasses
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class EventType(str, Enum):
    CHAT_MESSAGE = "CHAT_MESSAGE"
    MESSAGE_ANALYZED = "MESSAGE_ANALYZED"
    SOCIAL_DECISION = "SOCIAL_DECISION"
    MEMORY_CANDIDATE = "MEMORY_CANDIDATE"
    TICK = "TICK"
    SCREEN_CHANGED = "SCREEN_CHANGED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"
    USER_DECISION_REQUIRED = "USER_DECISION_REQUIRED"
    USER_DECISION_RECEIVED = "USER_DECISION_RECEIVED"
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"


def _serialise(value: Any) -> Any:
    """Convert known runtime objects to JSON-compatible data without I/O."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _serialise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialise(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _serialise(to_dict())
    if dataclasses.is_dataclass(value):
        return _serialise(dataclasses.asdict(value))
    return str(value)


@dataclass(slots=True)
class PlayerEvent:
    event_type: EventType
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "created_at": self.created_at,
            "source": self.source,
            "payload": _serialise(self.payload),
        }

    @classmethod
    def from_chat_message(cls, message: Any) -> "PlayerEvent":
        """Bridge the proven ChatCapture output without parsing logcat again."""
        return cls(
            event_type=EventType.CHAT_MESSAGE,
            source="chat_capture",
            payload={"message": _serialise(message)},
        )

    @classmethod
    def tick(cls) -> "PlayerEvent":
        return cls(event_type=EventType.TICK, source="player_loop")

