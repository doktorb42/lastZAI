"""Historia rozmów i pamięć SQLite mają jedno źródło w Mózgu."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import mozg.conversation_store as legacy
from mozg import conversation_store as active


class ConversationStoreCompatibilityTests(unittest.TestCase):
    def test_old_and_new_paths_share_store_and_memory_types(self) -> None:
        self.assertIs(legacy.ConversationStore, active.ConversationStore)
        self.assertIs(legacy.PlayerMemory, active.PlayerMemory)

    def test_store_uses_explicit_temporary_database_and_closes_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "conversation.db"
            with active.ConversationStore(path) as store:
                memory_id = store.add_memory(
                    player_uid="test-player",
                    memory_type="identity",
                    content="test fact",
                )
                self.assertGreater(memory_id, 0)
                self.assertEqual(len(store.get_memories(player_uid="test-player")), 1)


if __name__ == "__main__":
    unittest.main()
