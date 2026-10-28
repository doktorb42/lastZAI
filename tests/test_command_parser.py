from __future__ import annotations

import unittest

from mozg.command_parser import CommandParser, CommandType, RiskLevel
from mozg.pending_commands import PendingCommandGoal, PendingCommandStatus


class CommandParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = CommandParser()

    def test_recognizes_private_action_commands_without_execution(self) -> None:
        reinforce = self.parser.parse("reinforce me", sender_uid="p1", sender_name="Alex")
        rally = self.parser.parse("join my rally", sender_uid="p1")
        wait = self.parser.parse("wait 2 min")
        self.assertEqual(reinforce.command_type, CommandType.REINFORCE_PLAYER)
        self.assertEqual(reinforce.target_player_uid, "p1")
        self.assertEqual(rally.command_type, CommandType.JOIN_RALLY)
        self.assertEqual(wait.command_type, CommandType.WAIT)
        self.assertEqual(wait.duration_seconds, 120)

    def test_high_risk_commands_require_confirmation(self) -> None:
        attack = self.parser.parse("attack him", sender_uid="p1")
        teleport = self.parser.parse("teleport here", sender_uid="p1")
        self.assertEqual(attack.risk_level, RiskLevel.HIGH)
        self.assertTrue(attack.requires_confirmation)
        self.assertTrue(teleport.requires_confirmation)
        pending = PendingCommandGoal.from_parsed(attack, sender_uid="p1")
        self.assertEqual(pending.status, PendingCommandStatus.WAITING)
        self.assertNotEqual(pending.status, PendingCommandStatus.EXECUTED)

    def test_polish_commands_are_classified_but_never_executed(self) -> None:
        cases = {
            "wyślij posiłki": CommandType.REINFORCE_PLAYER,
            "dołącz do mojego rajdu": CommandType.JOIN_RALLY,
            "przyjdź do mnie": CommandType.COME_TO_PLAYER,
            "otwórz mapę": CommandType.OPEN_MAP,
            "sprawdź prywatne": CommandType.CHECK_PRIVATE_CHAT,
            "sprawdź sojusz": CommandType.CHECK_ALLIANCE_CHAT,
            "wróć do bazy": CommandType.RETURN_TO_BASE,
            "czekaj 2 minuty": CommandType.WAIT,
            "tarcza": CommandType.SHIELD,
            "teleportuj tutaj": CommandType.TELEPORT,
            "zaatakuj go": CommandType.ATTACK_PLAYER,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = self.parser.parse(text, sender_uid="p1")
                self.assertEqual(parsed.command_type, expected)
                if expected in {CommandType.TELEPORT, CommandType.ATTACK_PLAYER}:
                    self.assertTrue(parsed.requires_confirmation)
        self.assertEqual(self.parser.parse("czekaj 2 minuty").duration_seconds, 120)

    def test_unknown_text_is_not_an_action(self) -> None:
        unknown = self.parser.parse("nice lol")
        self.assertEqual(unknown.command_type, CommandType.UNKNOWN)
        self.assertFalse(unknown.is_actionable)


if __name__ == "__main__":
    unittest.main()
