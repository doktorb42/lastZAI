"""Deterministyczny evaluator benchmarku ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.benchmark_evaluator as legacy
import trening.benchmark_evaluator as training


class BenchmarkEvaluatorMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_offline_evaluator(self) -> None:
        self.assertIs(legacy.run_private_benchmark, training.run_private_benchmark)
        self.assertEqual(legacy.DEFAULT_BENCHMARK, training.DEFAULT_BENCHMARK)
        self.assertTrue(training.DEFAULT_BENCHMARK.is_file())


if __name__ == "__main__":
    unittest.main()
