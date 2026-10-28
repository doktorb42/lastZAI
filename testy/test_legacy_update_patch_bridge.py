"""Silnik patcha ma jedno źródło poza katalogiem głównym."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import archive.legacy_runtime.update_patch as legacy
from archive.legacy_runtime import update_patch


class LegacyUpdatePatchBridgeTests(unittest.TestCase):
    def test_bridge_exposes_the_archived_patch_engine(self) -> None:
        self.assertIs(legacy.PatchAbort, update_patch.PatchAbort)
        self.assertIs(legacy.main, update_patch.main)
        self.assertEqual(legacy.DEFAULT_PACKAGE, update_patch.DEFAULT_PACKAGE)

    def test_bridge_forwards_temporary_constant_overrides(self) -> None:
        original = update_patch.DEFAULT_DEVICE
        with patch.object(legacy, "DEFAULT_DEVICE", "fake-device"):
            self.assertEqual(update_patch.DEFAULT_DEVICE, "fake-device")
        self.assertEqual(update_patch.DEFAULT_DEVICE, original)


if __name__ == "__main__":
    unittest.main()
