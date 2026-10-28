"""Analizator intencji Mózgu ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import mozg.message_intent as legacy
import mozg.message_intent as brain


class MessageIntentMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_analyzer_and_enum(self) -> None:
        self.assertIs(legacy.MessageIntentAnalyzer, brain.MessageIntentAnalyzer)
        self.assertIs(legacy.MessageIntent, brain.MessageIntent)

    def test_spam_remains_ignored_without_a_model(self) -> None:
        result = brain.MessageIntentAnalyzer(ai=None).analyze(
            {"text": "aaaaaaaaaaaa", "sender_uid": "uid", "group": "custom"}
        )
        self.assertEqual(result.intent, brain.MessageIntent.IGNORE)


if __name__ == "__main__":
    unittest.main()
