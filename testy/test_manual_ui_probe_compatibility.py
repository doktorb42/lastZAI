"""Ręczny probe UI jest narzędziem testowym, nie częścią runtime PEva."""

from __future__ import annotations

from pathlib import Path
import unittest

import testy.manual_ui_probe as legacy
from testy import manual_ui_probe as probe


class ManualUiProbeCompatibilityTests(unittest.TestCase):
    def test_old_entry_and_test_tool_share_the_same_functions(self) -> None:
        self.assertIs(legacy.collect_xml, probe.collect_xml)
        self.assertIs(legacy.main, probe.main)
        self.assertEqual(probe.ROOT, Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    unittest.main()
