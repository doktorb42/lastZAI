"""Testy nowej publicznej ścieżki Charakteru PEva."""

from __future__ import annotations

import unittest

from charakter import load_personality, load_peva_self


class CharacterPackageTests(unittest.TestCase):
    def test_personality_and_identity_load_from_active_config(self) -> None:
        personality = load_personality()
        identity = load_peva_self()

        self.assertEqual(identity.name, "PEva")
        self.assertGreater(personality.playfulness, 0.0)
        self.assertIn("customer-support", personality.prompt_line())


if __name__ == "__main__":
    unittest.main()
