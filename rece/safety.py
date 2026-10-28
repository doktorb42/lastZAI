"""Centralny bezpiecznik dla każdej przyszłej akcji Rąk."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import Action, ActionKind


@dataclass(frozen=True, slots=True)
class SafetyState:
    safe: bool
    reason: str


class SafetyGate:
    """Domyślnie blokuje działania; `resume` wymaga świadomego wywołania."""

    def __init__(self) -> None:
        self._safe = True
        self._reason = "PEva started in SAFE mode"

    @property
    def state(self) -> SafetyState:
        return SafetyState(self._safe, self._reason)

    def stop(self, reason: str = "Stopped by user") -> SafetyState:
        self._safe = True
        self._reason = reason.strip() or "Stopped by user"
        return self.state

    def resume(self, reason: str = "Resumed by user") -> SafetyState:
        self._safe = False
        self._reason = reason.strip() or "Resumed by user"
        return self.state

    def allows(self, action: Action) -> bool:
        """Allow observation-only no-op, otherwise honour the global stop."""
        if action.kind is ActionKind.NONE:
            return True
        return not self._safe
