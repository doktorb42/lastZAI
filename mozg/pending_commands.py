"""Preparation-only command goals. No object here reaches ActionExecutor."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mozg.command_parser import ParsedCommand, RiskLevel


class PendingCommandStatus(str, Enum):
    DETECTED = "DETECTED"
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


@dataclass(slots=True)
class PendingCommandGoal:
    command_id: str
    command_type: str
    sender_uid: str
    created_at: float
    status: PendingCommandStatus
    risk_level: RiskLevel
    requires_confirmation: bool
    parsed_arguments: dict[str, Any]

    @classmethod
    def from_parsed(cls, command: ParsedCommand, *, sender_uid: str) -> "PendingCommandGoal":
        return cls(
            command_id=uuid.uuid4().hex,
            command_type=command.command_type.value,
            sender_uid=str(sender_uid),
            created_at=time.time(),
            status=(PendingCommandStatus.WAITING if command.requires_confirmation else PendingCommandStatus.DETECTED),
            risk_level=command.risk_level,
            requires_confirmation=command.requires_confirmation,
            parsed_arguments=command.to_dict(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type,
            "sender_uid": self.sender_uid,
            "created_at": self.created_at,
            "status": self.status.value,
            "risk_level": self.risk_level.value,
            "requires_confirmation": self.requires_confirmation,
            "parsed_arguments": dict(self.parsed_arguments),
        }
