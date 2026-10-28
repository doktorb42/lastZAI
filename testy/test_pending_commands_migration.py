"""Kolejka oczekujących poleceń Mózgu ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import mozg.command_parser as parser
import mozg.pending_commands as brain
import mozg.pending_commands as legacy
class PendingCommandsMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_goal_type(self) -> None:
        self.assertIs(legacy.PendingCommandGoal, brain.PendingCommandGoal)
        self.assertIs(legacy.PendingCommandStatus, brain.PendingCommandStatus)

    def test_high_risk_command_stays_waiting_for_confirmation(self) -> None:
        parsed = parser.CommandParser().parse("attack him", sender_uid="uid")
        goal = brain.PendingCommandGoal.from_parsed(parsed, sender_uid="uid")
        self.assertEqual(goal.status, brain.PendingCommandStatus.WAITING)
        self.assertTrue(goal.requires_confirmation)


if __name__ == "__main__":
    unittest.main()
