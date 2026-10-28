"""Budownik danych treningowych ma jedno źródło po migracji."""

from __future__ import annotations

import unittest

import trening.dataset_builder as legacy
import trening.dataset_builder as training


class DatasetBuilderMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_training_types(self) -> None:
        self.assertIs(legacy.DatasetBuilder, training.DatasetBuilder)
        self.assertIs(legacy.TrainingSample, training.TrainingSample)
        self.assertIs(legacy.RAW_DIR, training.RAW_DIR)
        self.assertIs(legacy.PROCESSED_DIR, training.PROCESSED_DIR)


if __name__ == "__main__":
    unittest.main()
