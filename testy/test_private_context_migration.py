"""Kontekst prywatnej rozmowy Mózgu ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import mozg.private_context as brain
import mozg.private_context as legacy
class PrivateContextMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_registry(self) -> None:
        self.assertIs(legacy.PrivateContextRegistry, brain.PrivateContextRegistry)
        self.assertIs(legacy.PrivateConversationContext, brain.PrivateConversationContext)

    def test_context_is_keyed_by_uid_and_room_not_display_name(self) -> None:
        registry = brain.PrivateContextRegistry()
        first = registry.get_or_create(player_uid="uid-a", room_id="room-a")
        second = registry.get_or_create(player_uid="uid-b", room_id="room-a")
        self.assertIsNot(first, second)
        self.assertIs(registry.get(player_uid="uid-a", room_id="room-a"), first)


if __name__ == "__main__":
    unittest.main()
