"""Polityka podglądu odpowiedzi społecznej ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import charakter.social_reply_policy as character
import charakter.social_reply_policy as legacy
class SocialReplyPolicyMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_policy(self) -> None:
        self.assertIs(legacy.PrivateSocialReplyPolicy, character.PrivateSocialReplyPolicy)
        self.assertIs(legacy.SocialReplyPreviewDecision, character.SocialReplyPreviewDecision)

    def test_clear_closure_stays_silent(self) -> None:
        decision = character.PrivateSocialReplyPolicy().evaluate(
            {"group": "custom", "intent": "THANKS"}, "thanks"
        )
        self.assertFalse(decision.should_generate_preview)
        self.assertEqual(decision.reason, "clear_conversation_close")


if __name__ == "__main__":
    unittest.main()
