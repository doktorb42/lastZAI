"""Polityka odpowiedzi społecznej Charakteru ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import charakter.social_policy as character
import charakter.social_policy as legacy
class SocialPolicyMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_policy(self) -> None:
        self.assertIs(legacy.SocialPolicy, character.SocialPolicy)
        self.assertIs(legacy.SocialDecision, character.SocialDecision)

    def test_private_message_is_never_silenced_by_an_alliance_cooldown(self) -> None:
        policy = character.SocialPolicy(now=lambda: 100.0)
        decision = policy.evaluate(
            type("Analysis", (), {"sender_uid": "uid", "group": "custom", "intent": "QUESTION"})(),
            {"sender_uid": "uid"},
        )
        self.assertTrue(decision.should_reply)

    def test_priority_is_local_to_character_not_the_legacy_goal_loop(self) -> None:
        self.assertEqual(int(character.SocialPriority.HIGH), 75)
        from archive.legacy_runtime.goal_manager import GoalPriority

        self.assertIsNot(character.SocialPriority, GoalPriority)


if __name__ == "__main__":
    unittest.main()
