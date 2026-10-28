"""Przekształcanie pełnego kontekstu w bezpieczną decyzję PEva.

Na tym etapie decyzje są wyłącznie propozycjami odpowiedzi.  Ten moduł nie
zna Rąk i nie ma możliwości kliknięcia, wpisania ani wysłania czegokolwiek.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from contracts import BrainInput, Decision, DecisionKind, MessageObservation
from .lastz_ai import AIReply
from .action_claim_guard import ActionClaimGuard
from .reply_quality_gate import ReplyQualityGate


class ReplyModel(Protocol):
    @property
    def status(self) -> object: ...

    def generate_reply_safe(self, incoming_text: str, **kwargs: object) -> AIReply: ...


class BrainDecisionEngine:
    """Tworzy jedną decyzję, ale nigdy nie wykonuje akcji w Last Z."""

    def __init__(
        self,
        model: ReplyModel,
        *,
        quality_gate: ReplyQualityGate | None = None,
        claim_guard: ActionClaimGuard | None = None,
    ) -> None:
        self.model = model
        self.quality_gate = quality_gate or ReplyQualityGate()
        self.claim_guard = claim_guard or ActionClaimGuard()

    def decide(
        self,
        context: BrainInput,
        *,
        history: Sequence[Mapping[str, str]] = (),
    ) -> Decision:
        messages = tuple(message for message in context.vision.messages if message.text and not message.nontext)
        if not messages:
            return Decision(DecisionKind.DO_NOTHING, "no_confirmed_text")

        message = messages[-1]
        if not message.player_name:
            # OCR text from the full screen is useful, but it does not prove
            # that the current view is a coherent private conversation.  A
            # preview cannot be offered before a Last Z-style header confirms
            # the open chat.  This is not a UID or player-identity resolver.
            return Decision(DecisionKind.WAIT, "conversation_header_not_confirmed")

        if not bool(getattr(self.model.status, "ready", False)):
            return Decision(DecisionKind.WAIT, "local_model_not_ready")

        extra_context = (*context.personality_context, *context.emotion_context, *context.memory_context)
        reply = self.model.generate_reply_safe(
            message.text or "",
            sender_name=message.player_name,
            group="custom",
            history=history,
            extra_context=extra_context,
        )
        if reply.error:
            return Decision(DecisionKind.WAIT, "local_model_error")
        verdict = self.quality_gate.validate(reply.text)
        if not verdict.accepted:
            return Decision(DecisionKind.DO_NOTHING, f"reply_rejected:{verdict.reason}")

        # PEva has not executed any game command in this new runtime.  Treat
        # a potentially action-claiming reply as unsafe even in preview.
        claim = self.claim_guard.validate(
            reply.text,
            pending_command={"status": "WAITING", "risk_level": "HIGH", "command_type": "game_action"},
        )
        if claim.changed:
            return Decision(DecisionKind.WAIT, "unconfirmed_action_claim")
        return Decision(DecisionKind.REPLY, "local_model_preview", text=reply.text.strip())


def latest_text_observation(context: BrainInput) -> MessageObservation | None:
    """Publiczna, mała pomoc do testów i przyszłego resolvera tożsamości."""
    for message in reversed(context.vision.messages):
        if message.text and not message.nontext:
            return message
    return None

