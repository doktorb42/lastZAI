"""Mały, jawny stan emocji; nie podejmuje decyzji i nie wykonuje akcji."""

from __future__ import annotations

from dataclasses import dataclass, replace


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True, slots=True)
class EmotionState:
    """Bieżący nastrój PEva w skali 0–1, bez trwałej pamięci gracza."""

    mood: float = 0.55
    affinity: float = 0.50
    irritation: float = 0.05
    interest: float = 0.50
    energy: float = 0.60
    last_reason: str = "initial state"

    def __post_init__(self) -> None:
        for name in ("mood", "affinity", "irritation", "interest", "energy"):
            if not 0.0 <= float(getattr(self, name)) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    def context_lines(self) -> tuple[str, ...]:
        """Krótki, opisowy kontekst dla Mózgu, nie instrukcja dla modelu."""
        return (
            f"mood={self.mood:.2f}",
            f"affinity={self.affinity:.2f}",
            f"irritation={self.irritation:.2f}",
            f"interest={self.interest:.2f}",
            f"energy={self.energy:.2f}",
        )


class Emotions:
    """Kontroluje jeden bieżący stan i ogranicza każdą zmianę do skali 0–1."""

    def __init__(self, initial: EmotionState | None = None) -> None:
        self._state = initial or EmotionState()

    @property
    def state(self) -> EmotionState:
        return self._state

    def adjust(
        self,
        *,
        reason: str,
        mood: float = 0.0,
        affinity: float = 0.0,
        irritation: float = 0.0,
        interest: float = 0.0,
        energy: float = 0.0,
    ) -> EmotionState:
        """Zastosuj małe zmiany, bez pozwalania wartościom wyjść poza skalę."""
        self._state = replace(
            self._state,
            mood=_unit(self._state.mood + mood),
            affinity=_unit(self._state.affinity + affinity),
            irritation=_unit(self._state.irritation + irritation),
            interest=_unit(self._state.interest + interest),
            energy=_unit(self._state.energy + energy),
            last_reason=reason.strip() or "unspecified adjustment",
        )
        return self._state

    def reset(self) -> EmotionState:
        self._state = EmotionState()
        return self._state
