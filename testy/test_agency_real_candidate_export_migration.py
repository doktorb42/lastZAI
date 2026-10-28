"""Eksport kandydatów rzeczywistych Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_real_candidate_export as legacy
import trening.agency_real_candidate_export as training


class AgencyRealCandidateExportMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_review_only_export(self) -> None:
        self.assertIs(legacy.export_real_candidates, training.export_real_candidates)
        self.assertIs(legacy.write_export, training.write_export)
        self.assertEqual(legacy.OUTPUT, training.OUTPUT)


if __name__ == "__main__":
    unittest.main()
