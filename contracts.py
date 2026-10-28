"""Małe, wspólne dane przekazywane między częściami PEva.

Ten moduł nie zna OCR, modelu, bazy ani ADB. Dzięki temu Oczy mogą wymienić
metodę odczytu, a Mózg i Ręce nadal otrzymają ten sam prosty format danych.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DecisionKind(str, Enum):
    DO_NOTHING = "DO_NOTHING"
    WAIT = "WAIT"
    REPLY = "REPLY"
    ASK_BACK = "ASK_BACK"
    CHANGE_TOPIC = "CHANGE_TOPIC"
    START_CONVERSATION = "START_CONVERSATION"


class ActionKind(str, Enum):
    NONE = "NONE"
    TAP = "TAP"
    SWIPE = "SWIPE"
    INPUT_TEXT = "INPUT_TEXT"
    SEND_MESSAGE = "SEND_MESSAGE"


@dataclass(frozen=True, slots=True)
class MessageObservation:
    """Jedna wiadomość rozpoznana przez Oczy, tylko w pamięci runtime."""

    text: str | None
    player_name: str | None
    confidence: float
    source: str
    nontext: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("message confidence must be between 0 and 1")
        if self.nontext and self.text:
            raise ValueError("a nontext observation cannot claim recognized text")


@dataclass(frozen=True, slots=True)
class VisionResult:
    """Wynik Oczu niezależny od tego, czy źródłem był OCR czy inny odczyt."""

    screen: str
    confidence: float
    messages: tuple[MessageObservation, ...] = ()
    event: str | None = None

    def __post_init__(self) -> None:
        if not self.screen.strip():
            raise ValueError("screen must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("vision confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class BrainInput:
    """Pełny kontekst przekazywany do Mózgu dla jednej decyzji."""

    vision: VisionResult
    memory_context: tuple[str, ...] = ()
    emotion_context: tuple[str, ...] = ()
    personality_context: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Decision:
    """Decyzja Mózgu; sama w sobie nie wykonuje żadnego działania."""

    kind: DecisionKind
    reason: str
    text: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        text_required = {DecisionKind.REPLY, DecisionKind.ASK_BACK, DecisionKind.CHANGE_TOPIC}
        if self.kind in text_required and not (self.text or "").strip():
            raise ValueError(f"{self.kind.value} requires text")
        if self.kind in {DecisionKind.DO_NOTHING, DecisionKind.WAIT} and self.text:
            raise ValueError(f"{self.kind.value} cannot contain outgoing text")


@dataclass(frozen=True, slots=True)
class Action:
    """Jedno polecenie dla Rąk, wykonywalne wyłącznie po przejściu SAFE."""

    kind: ActionKind
    target: str | None = None
    text: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind is ActionKind.SEND_MESSAGE and not (self.text or "").strip():
            raise ValueError("SEND_MESSAGE requires text")
        if self.kind is ActionKind.NONE and (self.target or self.text or self.metadata):
            raise ValueError("NONE cannot carry an action payload")
