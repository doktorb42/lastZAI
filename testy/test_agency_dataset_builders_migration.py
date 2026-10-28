"""Historyczne budowniki Agency V2–V4 mają jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_dataset_builder_v2 as legacy_v2
import trening.agency_dataset_builder_v3 as legacy_v3
import trening.agency_dataset_builder_v4 as legacy_v4
import trening.agency_dataset_builder_v2 as training_v2
import trening.agency_dataset_builder_v3 as training_v3
import trening.agency_dataset_builder_v4 as training_v4


class AgencyDatasetBuildersMigrationTests(unittest.TestCase):
    def test_all_legacy_modules_expose_their_single_training_builder(self) -> None:
        for legacy, training in (
            (legacy_v2, training_v2),
            (legacy_v3, training_v3),
            (legacy_v4, training_v4),
        ):
            self.assertIs(legacy.build, training.build)
            self.assertIs(legacy.source_rows, training.source_rows)
            self.assertEqual(legacy.MANIFEST_PATH, training.MANIFEST_PATH)


if __name__ == "__main__":
    unittest.main()
