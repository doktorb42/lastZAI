"""Audyt danych Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_dataset_audit as legacy
import trening.agency_dataset_audit as training


class AgencyDatasetAuditMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_read_only_audit(self) -> None:
        self.assertIs(legacy.audit_dataset, training.audit_dataset)
        self.assertIs(legacy.audit_combined_pilot, training.audit_combined_pilot)
        self.assertEqual(legacy.DEFAULT_DATASET, training.DEFAULT_DATASET)
        self.assertEqual(legacy.V1_EXPECTED_COUNTS, training.V1_EXPECTED_COUNTS)


if __name__ == "__main__":
    unittest.main()
