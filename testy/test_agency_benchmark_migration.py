"""Zamrożony benchmark Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import hashlib
import unittest

import trening.agency_benchmark as legacy
import trening.agency_benchmark as training


class AgencyBenchmarkMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_frozen_benchmark(self) -> None:
        self.assertIs(legacy.score_generated_cases, training.score_generated_cases)
        self.assertEqual(legacy.BENCHMARK, training.BENCHMARK)
        self.assertEqual(
            hashlib.sha256(training.BENCHMARK.read_bytes()).hexdigest(),
            "f11903411e2677d523e396c1e934965fa6f2da248a40aed7199b5ccf04157938",
        )


if __name__ == "__main__":
    unittest.main()
