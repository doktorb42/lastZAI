"""Deterministic command recognition; it never executes game actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CommandType(str, Enum):
    REINFORCE_PLAYER = "REINFORCE_PLAYER"
    JOIN_RALLY = "JOIN_RALLY"
    COME_TO_PLAYER = "COME_TO_PLAYER"
    OPEN_MAP = "OPEN_MAP"
    CHECK_PRIVATE_CHAT = "CHECK_PRIVATE_CHAT"
    CHECK_ALLIANCE_CHAT = "CHECK_ALLIANCE_CHAT"
    RETURN_TO_BASE = "RETURN_TO_BASE"
    WAIT = "WAIT"
    SHIELD = "SHIELD"
    TELEPORT = "TELEPORT"
    ATTACK_PLAYER = "ATTACK_PLAYER"
    UNKNOWN = "UNKNOWN"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(slots=True)
class ParsedCommand:
    command_type: CommandType
    confidence: float
    target_player_uid: str | None
    target_player_name: str | None
    target_location: str | None
    duration_seconds: int | None
    raw_text: str
    requires_confirmation: bool
    risk_level: RiskLevel

    @property
    def is_actionable(self) -> bool:
        return self.command_type != CommandType.UNKNOWN and self.confidence >= 0.70

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_type": self.command_type.value,
            "confidence": self.confidence,
            "target_player_uid": self.target_player_uid,
            "target_player_name": self.target_player_name,
            "target_location": self.target_location,
            "duration_seconds": self.duration_seconds,
            "raw_text": self.raw_text,
            "requires_confirmation": self.requires_confirmation,
            "risk_level": self.risk_level.value,
        }


class CommandParser:
    def parse(
        self,
        text: str,
        *,
        sender_uid: str | None = None,
        sender_name: str | None = None,
    ) -> ParsedCommand:
        raw = " ".join(str(text or "").split()).strip()
        lower = raw.casefold()
        target_uid = str(sender_uid) if sender_uid else None
        target_name = str(sender_name) if sender_name else None
        if re.search(r"\b(?:reinforce|reinforcements?|posiłk(?:i|ów|ami)?|wzmocn(?:ienie|ienia)?)\b", lower):
            return self._result(CommandType.REINFORCE_PLAYER, .96, raw, target_uid, target_name, risk=RiskLevel.MEDIUM)
        if re.search(r"\bjoin\b.*\brally\b|\brally\b.*\bjoin\b|\bdołącz(?:\s+do)?\s+(?:mojego\s+)?rajdu\b|\brajd\b.*\bdołącz\b", lower):
            return self._result(CommandType.JOIN_RALLY, .96, raw, target_uid, target_name, risk=RiskLevel.MEDIUM)
        if re.search(r"\b(?:come here|come to me|come over|przyjdź|chodź do mnie)\b", lower):
            return self._result(CommandType.COME_TO_PLAYER, .90, raw, target_uid, target_name, risk=RiskLevel.LOW)
        if re.search(r"\b(?:open|show)\b.*\bmap\b|\b(?:otwórz|pokaż)\b.*\bmap(?:ę|a)?\b", lower):
            return self._result(CommandType.OPEN_MAP, .92, raw, risk=RiskLevel.LOW)
        if re.search(r"\b(?:check|open)\b.*\b(?:private|pm)\b|\b(?:sprawdź|otwórz)\b.*\b(?:prywatn\w*|priv|pw)\b", lower):
            return self._result(CommandType.CHECK_PRIVATE_CHAT, .87, raw, risk=RiskLevel.LOW)
        if re.search(r"\b(?:check|open)\b.*\balliance\b|\b(?:sprawdź|otwórz)\b.*\bsojusz\w*\b", lower):
            return self._result(CommandType.CHECK_ALLIANCE_CHAT, .87, raw, risk=RiskLevel.LOW)
        if re.search(r"\b(?:go back|return)\b.*\bbase\b|\bwróć\b.*\bdo\s+baz\w*\b", lower):
            return self._result(CommandType.RETURN_TO_BASE, .95, raw, risk=RiskLevel.LOW)
        wait_match = re.search(r"\b(?:wait|czekaj)\s+(\d+)\s*(s|sec|secs|second|seconds|sek|sekund\w*|m|min|mins|minute|minutes|minut\w*)?\b", lower)
        if wait_match:
            amount = int(wait_match.group(1))
            unit = wait_match.group(2) or "s"
            seconds = amount * 60 if unit.startswith(("m", "minut")) else amount
            return self._result(CommandType.WAIT, .98, raw, duration_seconds=seconds, risk=RiskLevel.LOW)
        if re.search(r"\b(?:shield|tarcza)\b", lower):
            return self._result(CommandType.SHIELD, .90, raw, risk=RiskLevel.MEDIUM)
        if re.search(r"\b(?:teleport|teleportuj)\b", lower):
            return self._result(CommandType.TELEPORT, .94, raw, target_uid, target_name, risk=RiskLevel.HIGH, confirmation=True)
        if re.search(r"\b(?:attack|atakuj|zaatakuj)\b", lower):
            return self._result(CommandType.ATTACK_PLAYER, .78, raw, target_uid, target_name, risk=RiskLevel.HIGH, confirmation=True)
        return self._result(CommandType.UNKNOWN, .0, raw)

    @staticmethod
    def _result(
        command_type: CommandType,
        confidence: float,
        raw: str,
        target_uid: str | None = None,
        target_name: str | None = None,
        *,
        duration_seconds: int | None = None,
        risk: RiskLevel = RiskLevel.LOW,
        confirmation: bool = False,
    ) -> ParsedCommand:
        return ParsedCommand(
            command_type=command_type,
            confidence=confidence,
            target_player_uid=target_uid,
            target_player_name=target_name,
            target_location=None,
            duration_seconds=duration_seconds,
            raw_text=raw,
            requires_confirmation=confirmation,
            risk_level=risk,
        )
