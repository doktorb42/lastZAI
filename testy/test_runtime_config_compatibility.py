"""Konfiguracja ścieżek i stałych ma jedno źródło prawdy w config/."""

from __future__ import annotations

import unittest

import config.runtime_config as legacy
from config import runtime_config as active


class RuntimeConfigCompatibilityTests(unittest.TestCase):
    def test_old_and_new_paths_share_constants_and_room_parser(self) -> None:
        self.assertEqual(legacy.PROJECT_DIR, active.PROJECT_DIR)
        self.assertEqual(legacy.DATA_DIR, active.DATA_DIR)
        self.assertIs(legacy.parse_private_room, active.parse_private_room)
        self.assertEqual(
            legacy.parse_private_room("custom_10_20_v3"),
            ("10", "20", 3),
        )


if __name__ == "__main__":
    unittest.main()
