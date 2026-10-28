"""Historyczna nazwa ADB prowadzi do jednego archiwalnego modułu."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import archive.legacy_runtime.adb_controll as legacy
from archive.legacy_runtime import adb_controll


class LegacyAdbBridgeTests(unittest.TestCase):
    def test_root_name_is_the_archived_module_not_a_copy(self) -> None:
        self.assertIs(legacy, adb_controll)
        self.assertIs(legacy.send_private_reply_verified, adb_controll.send_private_reply_verified)

    def test_legacy_patch_targets_the_archived_function_and_restores_it(self) -> None:
        original = adb_controll.tap
        with patch.object(legacy, "tap", return_value=True) as replacement:
            self.assertIs(adb_controll.tap, replacement)
        self.assertIs(adb_controll.tap, original)


if __name__ == "__main__":
    unittest.main()
