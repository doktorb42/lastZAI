"""Parser poleceń Mózgu ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import mozg.command_parser as legacy
import mozg.command_parser as brain


class CommandParserMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_parser_and_types(self) -> None:
        self.assertIs(legacy.CommandParser, brain.CommandParser)
        self.assertIs(legacy.ParsedCommand, brain.ParsedCommand)

    def test_attack_is_labeled_high_risk_but_not_executed(self) -> None:
        command = brain.CommandParser().parse("attack him", sender_uid="uid")
        self.assertEqual(command.command_type, brain.CommandType.ATTACK_PLAYER)
        self.assertEqual(command.risk_level, brain.RiskLevel.HIGH)
        self.assertTrue(command.requires_confirmation)


if __name__ == "__main__":
    unittest.main()
