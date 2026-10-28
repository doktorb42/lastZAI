"""Niezmienne, syntetyczne korekty Agency mieszkają wyłącznie w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_corrective_dataset_v2 as legacy_v2
import trening.agency_corrective_dataset_v3 as legacy_v3
import trening.agency_corrective_dataset_v4 as legacy_v4
import trening.agency_corrective_dataset_v2 as training_v2
import trening.agency_corrective_dataset_v3 as training_v3
import trening.agency_corrective_dataset_v4 as training_v4


class AgencyCorrectiveSourcesMigrationTests(unittest.TestCase):
    def test_all_legacy_modules_expose_their_single_training_source(self) -> None:
        for legacy, training in (
            (legacy_v2, training_v2),
            (legacy_v3, training_v3),
            (legacy_v4, training_v4),
        ):
            self.assertIs(legacy.examples, training.examples)
            self.assertIs(legacy.validate, training.validate)
            self.assertEqual(legacy.OUTPUT, training.OUTPUT)


if __name__ == "__main__":
    unittest.main()
