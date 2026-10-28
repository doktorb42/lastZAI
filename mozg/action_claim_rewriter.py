"""Naturalne, chronione poprawki odpowiedzi z niepotwierdzoną akcją."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_PROMPT_PATH = Path("config") / "prompts" / "action_claim_rewrite_system.txt"


@dataclass(frozen=True, slots=True)
class RewriteAttempt:
    text: str
    attempted: bool
    error: str | None = None


class ActionClaimRewriter:
    """Użyj istniejącego lokalnego AI dopiero po zadziałaniu strażnika."""

    _fallbacks = {
        "REINFORCE_PLAYER": ("I'll check.", "give me a sec.", "lemme check.", "not yet, I'll check."),
        "JOIN_RALLY": ("I'll check the rally.", "give me a sec.", "lemme see."),
        "COME_TO_PLAYER": ("I'll check.", "one sec.", "lemme see where you are."),
        "WAIT": ("okay.", "got it.", "sure."),
        "SHIELD": ("I'll check.", "one sec."),
        "TELEPORT": ("not yet.", "I'll check first.", "lemme see."),
        "ATTACK_PLAYER": ("not yet.", "I haven't done anything yet.", "lemme check first."),
        "UNKNOWN": ("I'll check.", "give me a sec."),
    }
    _boilerplate_markers = (
        "as an ai", "language model", "i cannot assist", "i can't assist",
        "i am unable", "i'm unable", "here is the rewritten",
    )

    def __init__(self, prompt_path: str | Path = DEFAULT_PROMPT_PATH) -> None:
        self.prompt_path = Path(prompt_path)

    def rewrite_unsafe_reply(
        self,
        *,
        ai: Any,
        original_reply: str,
        incoming_message: Mapping[str, Any],
        parsed_command: Any | None,
        command_status: str | None,
        recent_private_context: Iterable[str] | None,
        confirmed_actions: Iterable[Any] | None,
        relationship: str | None,
        reply_length_mode: Any | None,
    ) -> RewriteAttempt:
        """Poproś o jedną krótką poprawkę; błąd zwraca bezpieczny fallback."""
        completer = getattr(ai, "complete_task_safe", None)
        if not callable(completer):
            return RewriteAttempt("", False, "Existing local AI completion API is unavailable.")
        system_prompt = self._load_prompt()
        if not system_prompt:
            return RewriteAttempt("", False, "Action-claim rewrite prompt is unavailable.")
        request = {
            "incoming_message": _clean(incoming_message.get("text"))[:600],
            "pending_command": _serializable(parsed_command),
            "command_status": str(command_status or "DETECTED"),
            "confirmed_actions": [_serializable(item) for item in (confirmed_actions or ())][-8:],
            "recent_context": [_clean(item)[:240] for item in (recent_private_context or ())][-12:],
            "relationship": str(relationship or "UNKNOWN"),
            "reply_length_mode": _enum_value(reply_length_mode) or "SHORT",
            "original_unsafe_reply": _clean(original_reply)[:600],
        }
        try:
            result = completer(
                system_prompt=system_prompt,
                user_prompt=json.dumps(request, ensure_ascii=False, sort_keys=True),
            )
        except Exception as exc:
            return RewriteAttempt("", True, f"Rewrite request failed: {type(exc).__name__}")
        error = str(getattr(result, "error", "") or "").strip()
        text = _clean(getattr(result, "text", ""))
        if error:
            return RewriteAttempt("", True, error)
        if not text:
            return RewriteAttempt("", True, "Rewrite returned empty text.")
        if self._looks_like_boilerplate(text):
            return RewriteAttempt("", True, "Rewrite returned AI boilerplate.")
        return RewriteAttempt(text, True)

    def deterministic_fallback(
        self,
        *,
        parsed_command: Any | None,
        source_message_key: str | None,
    ) -> str:
        command_type = _command_type(parsed_command)
        variants = self._fallbacks.get(command_type, self._fallbacks["UNKNOWN"])
        seed = f"{command_type}|{source_message_key or ''}"
        index = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % len(variants)
        return variants[index]

    def _load_prompt(self) -> str:
        try:
            return self.prompt_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _looks_like_boilerplate(self, text: str) -> bool:
        lower = text.casefold()
        return any(marker in lower for marker in self._boilerplate_markers)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _command_type(command: Any | None) -> str:
    if isinstance(command, Mapping):
        value = command.get("command_type")
    else:
        value = getattr(command, "command_type", None)
    return _enum_value(value).upper() or "UNKNOWN"


def _serializable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _serializable(item) for key, item in value.items()}
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        return _serializable(converter())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return [_serializable(item) for item in value]
    return str(value)
