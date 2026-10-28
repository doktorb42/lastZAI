"""Jedno miejsce łączenia Oczu, Charakteru, Emocji i przyszłej pamięci."""

from __future__ import annotations

from collections.abc import Iterable

from charakter import PevaPersonality, PevaSelf, load_peva_self, load_personality
from contracts import BrainInput, VisionResult
from emocje import Emotions


class BrainContextBuilder:
    """Buduje prosty `BrainInput`, bez wiedzy o OCR, ADB ani modelu."""

    def __init__(
        self,
        *,
        personality: PevaPersonality | None = None,
        identity: PevaSelf | None = None,
        emotions: Emotions | None = None,
    ) -> None:
        self.personality = personality or load_personality()
        self.identity = identity or load_peva_self()
        self.emotions = emotions or Emotions()

    def build(
        self,
        vision: VisionResult,
        *,
        memory_context: Iterable[str] = (),
    ) -> BrainInput:
        """Połącz dane w jednej strukturze gotowej dla przyszłego modelu."""
        memory = tuple(item.strip() for item in memory_context if item.strip())
        personality = (self.identity.prompt_block(), self.personality.prompt_line())
        return BrainInput(
            vision=vision,
            memory_context=memory,
            emotion_context=self.emotions.state.context_lines(),
            personality_context=personality,
        )
