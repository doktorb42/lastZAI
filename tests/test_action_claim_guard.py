from __future__ import annotations

import unittest

from mozg.action_claim_guard import ActionClaimGuard
from mozg.command_parser import CommandParser
from mozg.pending_commands import PendingCommandGoal, PendingCommandStatus


class ActionClaimGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = ActionClaimGuard()
        self.command = CommandParser().parse("reinforce me", sender_uid="p1")

    def test_unexecuted_command_cannot_claim_completion(self) -> None:
        result = self.guard.guard("done, reinforced", self.command, [])
        self.assertTrue(result.changed)
        self.assertNotIn("reinforced", result.text.casefold())
        self.assertIn("check first", result.text.casefold())

    def test_bare_yes_is_safe_for_pending_command_followup(self) -> None:
        pending = PendingCommandGoal.from_parsed(self.command, sender_uid="p1")
        result = self.guard.guard("yes", pending_command=pending)
        self.assertTrue(result.changed)
        self.assertTrue(result.text.casefold().startswith("i'll check"))

    def test_future_tense_reinforcement_claim_is_safe(self) -> None:
        result = self.guard.guard("Sure thing! I'll send reinforcements.", self.command, [])
        self.assertTrue(result.changed)
        self.assertEqual(result.text, "I'll check first.")

    def test_unconfirmed_acceptance_is_safe(self) -> None:
        result = self.guard.guard("Sure thing!", self.command, [])
        self.assertTrue(result.changed)
        self.assertEqual(result.text, "I'll check first.")

    def test_future_action_promise_is_safe(self) -> None:
        rally = CommandParser().parse("join my rally", sender_uid="p1")
        result = self.guard.validate("Sure, I'll join your rally.", rally, [])
        self.assertTrue(result.changed)

    def test_confirmed_action_does_not_get_rewritten(self) -> None:
        result = self.guard.guard(
            "done", self.command,
            [{"command_type": "REINFORCE_PLAYER", "status": "EXECUTED"}],
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.text, "done")


if __name__ == "__main__":
    unittest.main()
