"""Validated public Agency decisions.  No hidden reasoning is persisted."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ReactiveDecision(str, Enum):
    REPLY = "REPLY"
    IGNORE = "IGNORE"
    WAIT = "WAIT"
    ASK_BACK = "ASK_BACK"
    CHANGE_TOPIC = "CHANGE_TOPIC"
    PROPOSE_GAME_ACTION = "PROPOSE_GAME_ACTION"


class ProactiveDecision(str, Enum):
    START_CONVERSATION = "START_CONVERSATION"
    DO_NOTHING = "DO_NOTHING"


REACTIVE_WITH_MESSAGE = frozenset({
    ReactiveDecision.REPLY.value,
    ReactiveDecision.ASK_BACK.value,
    ReactiveDecision.CHANGE_TOPIC.value,
    ReactiveDecision.PROPOSE_GAME_ACTION.value,
})


@dataclass(frozen=True, slots=True)
class AgencyDecision:
    mode: str
    decision: str
    reason_code: str
    message: str | None = None
    target_uid: str | None = None
    raw: str | None = None

    @property
    def has_message(self) -> bool:
        return self.decision in REACTIVE_WITH_MESSAGE or self.decision == ProactiveDecision.START_CONVERSATION.value

    @property
    def is_no_send(self) -> bool:
        return self.decision in {ReactiveDecision.IGNORE.value, ReactiveDecision.WAIT.value, ProactiveDecision.DO_NOTHING.value}

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "message": self.message,
            "target_uid": self.target_uid,
        }

    @classmethod
    def parse_reactive(cls, raw: str | Mapping[str, Any]) -> "AgencyDecision":
        return cls._parse(raw, mode="AGENCY_REACTIVE", allowed={item.value for item in ReactiveDecision})

    @classmethod
    def parse_proactive(cls, raw: str | Mapping[str, Any], candidate_uids: set[str]) -> "AgencyDecision":
        result = cls.parse_proactive_shape(raw)
        if result.decision == ProactiveDecision.START_CONVERSATION.value:
            if not result.target_uid or result.target_uid not in candidate_uids:
                raise ValueError("proactive target_uid must be one of Python's safe candidates")
        return result

    @classmethod
    def parse_proactive_shape(cls, raw: str | Mapping[str, Any]) -> "AgencyDecision":
        """Validate proactive JSON independently from a runtime candidate set.

        Benchmark reporting uses this shape-level result to distinguish malformed
        output from a structurally valid response that selected an invalid UID.
        Runtime delivery continues to use :meth:`parse_proactive` above.
        """
        result = cls._parse(raw, mode="AGENCY_PROACTIVE", allowed={item.value for item in ProactiveDecision})
        if result.decision == ProactiveDecision.START_CONVERSATION.value and not result.target_uid:
            raise ValueError("START_CONVERSATION requires a target_uid")
        if result.decision == ProactiveDecision.DO_NOTHING.value and result.target_uid:
            raise ValueError("DO_NOTHING must not select a target")
        return result

    @classmethod
    def safe_fallback(cls, mode: str, reason_code: str = "MALFORMED_OUTPUT") -> "AgencyDecision":
        return cls(
            mode=mode,
            decision=ReactiveDecision.WAIT.value if mode == "AGENCY_REACTIVE" else ProactiveDecision.DO_NOTHING.value,
            reason_code=reason_code,
        )

    @classmethod
    def _parse(cls, raw: str | Mapping[str, Any], *, mode: str, allowed: set[str]) -> "AgencyDecision":
        source = raw if isinstance(raw, Mapping) else _json_object(str(raw or ""))
        if not isinstance(source, Mapping):
            raise ValueError("Agency output must be a compact JSON object")
        decision = _upper(source.get("decision"), "decision")
        if decision not in allowed:
            raise ValueError(f"unsupported Agency decision: {decision}")
        reason_code = _upper(source.get("reason_code"), "reason_code")
        if not re.fullmatch(r"[A-Z0-9_]{2,80}", reason_code):
            raise ValueError("reason_code must be a compact uppercase identifier")
        message = _optional_text(source.get("message"))
        target_uid = _optional_text(source.get("target_uid"))
        needs_message = decision in REACTIVE_WITH_MESSAGE or decision == ProactiveDecision.START_CONVERSATION.value
        if needs_message and not message:
            raise ValueError("selected decision requires a message")
        if not needs_message and message:
            raise ValueError("silent decision must not carry a message")
        if message and len(message) > 500:
            raise ValueError("Agency message is too long")
        return cls(mode=mode, decision=decision, reason_code=reason_code, message=message, target_uid=target_uid, raw=None if isinstance(raw, Mapping) else str(raw))


def _json_object(raw: str) -> Mapping[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("Agency JSON must be an object")
    return parsed


def _upper(value: Any, field: str) -> str:
    result = str(value or "").strip().upper()
    if not result:
        raise ValueError(f"Agency {field} is required")
    return result


def _optional_text(value: Any) -> str | None:
    result = " ".join(str(value or "").split()).strip()
    return result or None
