"""Testy składania kontekstu przez Mózg PEva."""

from __future__ import annotations

import unittest

from contracts import VisionResult
from mozg import BrainContextBuilder


class BrainContextTests(unittest.TestCase):
    def test_brain_receives_vision_personality_emotions_and_memory(self) -> None:
        builder = BrainContextBuilder()
        context = builder.build(
            VisionResult(screen="PRIVATE_CONVERSATION", confidence=0.97),
            memory_context=("Player likes short messages", ""),
        )

        self.assertEqual(context.vision.screen, "PRIVATE_CONVERSATION")
        self.assertEqual(context.memory_context, ("Player likes short messages",))
        self.assertTrue(context.emotion_context)
        self.assertTrue(context.personality_context)


if __name__ == "__main__":
    unittest.main()
