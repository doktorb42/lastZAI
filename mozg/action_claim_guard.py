"""Final deterministic safety pass for claims about unexecuted commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ClaimGuardResult:
    text: str
    changed: bool
    reason: str | None = None


class ActionClaimGuard:
    """Reject action-completion language unless the action is confirmed."""

    _unsafe = re.compile(
        r"\b(?:done|reinforced|sent\s+(?:troops|reinforcements)|(?:i(?:'ll| will)\s+)?send(?:ing)?\s+(?:troops|reinforcements)|joined(?:\s+the\s+rally)?|teleported|i(?:'m| am) coming|(?:i(?:'m| am)\s+)?(?:reinforcing|joining|sending|teleporting|attacking)\s+now|attack(?:ed)?\s+(?:him|them|now)|yes[,]?\s*(?:i\s*)?(?:did|have))\b",
        re.I,
    )
    _bare_confirmation = re.compile(r"^\s*(?:yes|yeah|yep|yup)\s*[!.]*\s*$", re.I)
    _action_commitment = re.compile(
        r"\b(?:sure thing|on it|will do|i(?:'m| am) doing it|i(?:'ll| will) do it|joining now|sending now|i got you|i(?:'m| am) coming|(?:i(?:'ll| will)|i can|i(?:'m| am) going to)\s+(?:reinforce|join|teleport|attack|send|come))\b",
        re.I,
    )
    _high_risk_acknowledgement = re.compile(r"^\s*(?:okay|ok|got it|sure)\s*[!.]*\s*$", re.I)

    def validate(
        self,
        generated_reply: str,
        parsed_command: Any | None = None,
        confirmed_actions: Iterable[Any] | None = None,
        *,
        pending_command: Any | None = None,
    ) -> ClaimGuardResult:
        """Return an unmodified reply plus a deterministic safety verdict."""
        text = " ".join(str(generated_reply or "").split()).strip()
        if not text:
            return ClaimGuardResult(text, False)
        command = parsed_command or pending_command
        if command is None or self._is_confirmed(command, confirmed_actions):
            return ClaimGuardResult(text, False)
        if (
            self._unsafe.search(text)
            or self._bare_confirmation.fullmatch(text)
            or self._action_commitment.search(text)
            or (self._is_high_risk(command) and self._high_risk_acknowledgement.fullmatch(text))
        ):
            return ClaimGuardResult(text, True, "Blocked an unconfirmed action claim or commitment.")
        return ClaimGuardResult(text, False)

    def guard(
        self,
        generated_reply: str,
        parsed_command: Any | None = None,
        confirmed_actions: Iterable[Any] | None = None,
        *,
        pending_command: Any | None = None,
    ) -> ClaimGuardResult:
        verdict = self.validate(
            generated_reply,
            parsed_command,
            confirmed_actions,
            pending_command=pending_command,
        )
        if verdict.changed:
            return ClaimGuardResult("I'll check first.", True, verdict.reason)
        return verdict

    @staticmethod
    def _is_confirmed(command: Any, confirmed_actions: Iterable[Any] | None) -> bool:
        command_type = _field_value(command, "command_type")
        if _field_value(command, "status").upper() == "EXECUTED":
            return True
        for item in confirmed_actions or ():
            status = _field_value(item, "status").upper()
            item_type = _field_value(item, "command_type")
            if status == "EXECUTED" and item_type == command_type:
                return True
        return False

    @staticmethod
    def _is_high_risk(command: Any) -> bool:
        return _field_value(command, "risk_level").upper() == "HIGH"


def _field_value(item: Any, field: str) -> str:
    if isinstance(item, dict):
        value = item.get(field)
    else:
        value = getattr(item, field, None)
    value = getattr(value, "value", value)
    return str(value or "")
