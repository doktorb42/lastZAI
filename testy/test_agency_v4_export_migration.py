"""Eksport GGUF V4 jest narzędziem Treningu, nigdy zwykłego runtime."""

from __future__ import annotations

import unittest
from pathlib import Path

import trening.export_agency_v4_gguf as legacy
from trening import export_agency_v4_gguf


class AgencyV4ExportMigrationTests(unittest.TestCase):
    def test_legacy_bridge_exports_the_training_implementation(self) -> None:
        self.assertIs(legacy.preflight, export_agency_v4_gguf.preflight)
        self.assertIs(legacy.main, export_agency_v4_gguf.main)
        self.assertEqual(export_agency_v4_gguf.ROOT, Path(__file__).resolve().parents[1])

    def test_export_paths_are_separate_from_the_runtime_model_selection(self) -> None:
        self.assertIn("peva-agency-pilot-v4-test", str(export_agency_v4_gguf.EXPORT_ROOT))
        self.assertNotIn("config", export_agency_v4_gguf.EXPORT_ROOT.parts)


if __name__ == "__main__":
    unittest.main()
