"""Polityka inicjatywy społecznej Charakteru ma jedno źródło po migracji."""

from __future__ import annotations

import random
import unittest

import charakter.social_initiative as character
import charakter.social_initiative as legacy
class SocialInitiativeMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_manager(self) -> None:
        self.assertIs(legacy.SocialInitiativeManager, character.SocialInitiativeManager)
        self.assertIs(legacy.InitiativeCandidate, character.InitiativeCandidate)

    def test_unknown_player_is_never_an_initiative_candidate(self) -> None:
        manager = character.SocialInitiativeManager(rng=random.Random(1), now=lambda: 10_000.0)
        decision = manager.choose(
            [{"player_uid": "unverified", "last_room_id": "room", "relationship": "UNKNOWN", "last_incoming_at": 1}],
            proactive_last_hour=0,
        )
        self.assertIsNone(decision.candidate)
        self.assertEqual(decision.reason, "unknown_player")


if __name__ == "__main__":
    unittest.main()
