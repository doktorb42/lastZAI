"""Współrzędne starego transportu mają jedno źródło poza katalogiem głównym."""

from __future__ import annotations

import unittest

import archive.legacy_runtime.coordinates as legacy
from archive.legacy_runtime import coordinates


class LegacyCoordinatesBridgeTests(unittest.TestCase):
    def test_bridge_exposes_the_archived_coordinates(self) -> None:
        self.assertEqual(legacy.CHAT_BUTTON, coordinates.CHAT_BUTTON)
        self.assertEqual(legacy.PRIVATE_CHAT_TAB, coordinates.PRIVATE_CHAT_TAB)
        self.assertEqual(legacy.WRITE_CHAT, coordinates.WRITE_CHAT)
        self.assertEqual(legacy.SEND_TEXT, coordinates.SEND_TEXT)


if __name__ == "__main__":
    unittest.main()
