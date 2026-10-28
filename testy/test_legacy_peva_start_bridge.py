"""Launcher patcha i Capture ma jedno źródło poza katalogiem głównym."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import archive.legacy_runtime.peva_start as legacy
from archive.legacy_runtime import peva_start


class LegacyPevaStartBridgeTests(unittest.TestCase):
    def test_bridge_exposes_the_archived_launcher(self) -> None:
        self.assertIs(legacy.main, peva_start.main)
        self.assertEqual(peva_start.ROOT, Path(__file__).resolve().parents[1])

    def test_bridge_forwards_a_temporary_patch_to_the_implementation(self) -> None:
        original = peva_start.TEST_DURATION
        with patch.object(legacy, "TEST_DURATION", 1):
            self.assertEqual(peva_start.TEST_DURATION, 1)
        self.assertEqual(peva_start.TEST_DURATION, original)


if __name__ == "__main__":
    unittest.main()
