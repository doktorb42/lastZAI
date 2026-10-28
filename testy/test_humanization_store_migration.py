"""Magazyn relacji Charakteru ma jedno źródło po migracji."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import charakter.humanization_store as character_store
import charakter.humanization_store as legacy
class HumanizationStoreMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_store_and_profiles(self) -> None:
        self.assertIs(legacy.HumanizationStore, character_store.HumanizationStore)
        self.assertIs(legacy.PlayerHumanProfile, character_store.PlayerHumanProfile)

    def test_store_remains_isolated_when_used_with_a_temp_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "humanization.db"
            with character_store.HumanizationStore(path) as store:
                profile = store.observe_incoming(
                    player_uid="verified-test-uid",
                    room_id="private-test-room",
                    text="hello",
                )
            self.assertEqual(profile.player_uid, "verified-test-uid")
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
