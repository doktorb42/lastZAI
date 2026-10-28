"""Runnery historycznych treningów Agency mają jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.train_agency_pilot_v2 as legacy_v2
import trening.train_agency_pilot_v3 as legacy_v3
import trening.train_agency_pilot_v4 as legacy_v4
import trening.train_agency_pilot_v2 as training_v2
import trening.train_agency_pilot_v3 as training_v3
import trening.train_agency_pilot_v4 as training_v4


class AgencyTrainersMigrationTests(unittest.TestCase):
    def test_legacy_imports_expose_the_same_hash_gates(self) -> None:
        for legacy, training in (
            (legacy_v2, training_v2),
            (legacy_v3, training_v3),
            (legacy_v4, training_v4),
        ):
            self.assertIs(legacy._frozen_benchmark_status, training._frozen_benchmark_status)
            self.assertEqual(legacy.FROZEN_BENCHMARK_SHA256, training.FROZEN_BENCHMARK_SHA256)
            self.assertTrue(training._frozen_benchmark_status()["matches_expected"])


if __name__ == "__main__":
    unittest.main()
