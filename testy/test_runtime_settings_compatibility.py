"""Zgodność starej nazwy ustawień po uporządkowaniu katalogu config."""

from __future__ import annotations

import unittest

import config.runtime_settings as legacy
from config import runtime_settings as active


class RuntimeSettingsCompatibilityTests(unittest.TestCase):
    def test_old_and_new_imports_share_settings_types_and_loader(self) -> None:
        self.assertIs(legacy.PevaSettings, active.PevaSettings)
        self.assertIs(legacy.load_settings, active.load_settings)
        self.assertEqual(legacy.PROJECT_ROOT, active.PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
