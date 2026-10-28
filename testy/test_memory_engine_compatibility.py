"""Silnik pamięci ma jedno źródło w Mózgu i nie startuje modelu przy imporcie."""

from __future__ import annotations

import unittest

import mozg.memory_engine as legacy
from mozg import memory_engine as active


class MemoryEngineCompatibilityTests(unittest.TestCase):
    def test_old_and_new_paths_share_memory_types(self) -> None:
        self.assertIs(legacy.MemoryEngine, active.MemoryEngine)
        self.assertIs(legacy.MemoryEngineConfig, active.MemoryEngineConfig)
        self.assertIs(legacy.MemoryCandidate, active.MemoryCandidate)

    def test_default_config_is_a_pure_data_object(self) -> None:
        config = active.MemoryEngineConfig()

        self.assertGreater(config.max_candidates_per_message, 0)
        self.assertTrue(config.skip_trivial_messages)


if __name__ == "__main__":
    unittest.main()
