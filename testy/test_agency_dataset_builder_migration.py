"""Budownik danych Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_dataset_builder as legacy
import trening.agency_dataset_builder as training


class AgencyDatasetBuilderMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_builder_and_paths(self) -> None:
        self.assertIs(legacy.build, training.build)
        self.assertIs(legacy._messages, training._messages)
        self.assertEqual(legacy.MANIFEST_PATH, training.MANIFEST_PATH)
        self.assertEqual(legacy.SEED, training.SEED)


if __name__ == "__main__":
    unittest.main()
