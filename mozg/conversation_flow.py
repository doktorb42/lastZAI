"""Small, RAM-only controller for one private conversation turn.

The OCR layer already collects a short burst of changed screens.  This module
adds the other half of a normal conversation: it remembers that the current
turn was handled and refuses to generate another reply for the exact same
visible context.  It stores only a hash, never player text or a transcript.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Iterable, Mapping


class ConversationPhase(str, Enum):
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    THINKING = "THINKING"
    COOLDOWN = "COOLDOWN"


@dataclass(frozen=True, slots=True)
class ConversationFlowDecision:
    allowed: bool
    reason: str


class ConversationFlow:
    """Prevent duplicate replies while retaining no chat content.

    ``PacketBurstBuffer`` is responsible for waiting for a short sequence of
    incoming messages.  Once that burst is ready, this controller makes sure
    a redraw of the same bubbles cannot make PEva answer twice.
    """

    def __init__(self, *, duplicate_window_seconds: float = 90.0) -> None:
        if not 10.0 <= float(duplicate_window_seconds) <= 600.0:
            raise ValueError("duplicate_window_seconds must be between 10 and 600")
        self._duplicate_window_seconds = float(duplicate_window_seconds)
        self._phase = ConversationPhase.IDLE
        self._reason = "waiting_for_a_private_message"
        self._last_fingerprint: str | None = None
        self._last_completed_at: float | None = None
        self._lock = RLock()

    def begin_collecting(self) -> None:
        with self._lock:
            self._phase = ConversationPhase.COLLECTING
            self._reason = "collecting_message_burst"

    def permit(
        self,
        *,
        conversation_header: str,
        incoming_text: str,
        history: Iterable[Mapping[str, str]],
        now: float | None = None,
    ) -> ConversationFlowDecision:
        """Allow exactly one model call for a unique visible turn."""
        current = time.monotonic() if now is None else float(now)
        fingerprint = _fingerprint(conversation_header, incoming_text, history)
        with self._lock:
            if (
                fingerprint == self._last_fingerprint
                and self._last_completed_at is not None
                and current - self._last_completed_at < self._duplicate_window_seconds
            ):
                self._phase = ConversationPhase.COOLDOWN
                self._reason = "same_visible_turn_already_handled"
                return ConversationFlowDecision(False, self._reason)
            self._phase = ConversationPhase.THINKING
            self._reason = "new_message_burst_ready"
            return ConversationFlowDecision(True, self._reason)

    def complete_turn(
        self,
        *,
        conversation_header: str,
        incoming_text: str,
        history: Iterable[Mapping[str, str]],
        now: float | None = None,
    ) -> None:
        current = time.monotonic() if now is None else float(now)
        fingerprint = _fingerprint(conversation_header, incoming_text, history)
        with self._lock:
            self._last_fingerprint = fingerprint
            self._last_completed_at = current
            self._phase = ConversationPhase.COOLDOWN
            self._reason = "turn_handled_waiting_for_new_message"

    def defer(self, reason: str) -> None:
        """Return to waiting without marking a turn as handled.

        A temporary local-model failure must not make PEva forget a legitimate
        player message merely because the model was still starting.
        """
        with self._lock:
            self._phase = ConversationPhase.IDLE
            self._reason = str(reason or "waiting_for_a_private_message")

    def reset(self, reason: str = "waiting_for_a_private_message") -> None:
        with self._lock:
            self._phase = ConversationPhase.IDLE
            self._reason = reason
            self._last_fingerprint = None
            self._last_completed_at = None

    def snapshot(self) -> dict[str, object]:
        """Public status only; the fingerprint is intentionally not exposed."""
        with self._lock:
            return {
                "phase": self._phase.value,
                "reason": self._reason,
                "duplicate_protection_active": self._last_fingerprint is not None,
            }


def _fingerprint(
    conversation_header: str,
    incoming_text: str,
    history: Iterable[Mapping[str, str]],
) -> str:
    pieces = [" ".join(str(conversation_header or "").casefold().split())]
    for turn in history:
        role = " ".join(str(turn.get("role", "")).casefold().split())
        content = " ".join(str(turn.get("content", "")).casefold().split())
        pieces.append(f"{role}:{content}")
    pieces.append("user:" + " ".join(str(incoming_text or "").casefold().split()))
    return hashlib.sha256("\n".join(pieces).encode("utf-8")).hexdigest()
