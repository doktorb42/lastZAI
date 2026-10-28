"""Zgodność starej ścieżki klienta modelu po przeniesieniu do Mózgu."""

from __future__ import annotations

import unittest

import mozg.lastz_ai as legacy
import mozg.lastz_ai as brain


class LastZAICompatibilityTests(unittest.TestCase):
    def test_legacy_and_brain_imports_share_the_same_public_types(self) -> None:
        self.assertIs(legacy.AIConfig, brain.AIConfig)
        self.assertIs(legacy.AIReply, brain.AIReply)
        self.assertIs(legacy.LastZAI, brain.LastZAI)

    def test_legacy_config_does_not_start_a_model(self) -> None:
        config = legacy.AIConfig()

        self.assertEqual(config.base_url, "http://127.0.0.1:8080")
        self.assertGreater(config.max_tokens, 0)


if __name__ == "__main__":
    unittest.main()
