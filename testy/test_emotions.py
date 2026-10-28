"""Testy małego stanu emocji PEva."""

from __future__ import annotations

import unittest

from emocje import Emotions


class EmotionTests(unittest.TestCase):
    def test_emotions_are_clamped_and_exposed_as_brain_context(self) -> None:
        emotions = Emotions()
        changed = emotions.adjust(reason="repeated spam", irritation=2.0, energy=-2.0)

        self.assertEqual(changed.irritation, 1.0)
        self.assertEqual(changed.energy, 0.0)
        self.assertEqual(changed.last_reason, "repeated spam")
        self.assertIn("irritation=1.00", changed.context_lines())


if __name__ == "__main__":
    unittest.main()
