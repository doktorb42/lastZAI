"""Benchmark modelu należy do Treningu i nie startuje serwera przy imporcie."""

from __future__ import annotations

import unittest
from pathlib import Path

import trening.model_benchmark as legacy
from trening import model_benchmark


ROOT = Path(__file__).resolve().parents[1]


class ModelBenchmarkMigrationTests(unittest.TestCase):
    def test_legacy_bridge_exports_the_training_implementation(self) -> None:
        self.assertIs(legacy.main, model_benchmark.main)
        self.assertIs(legacy.run, model_benchmark.run)
        self.assertEqual(model_benchmark.ROOT, ROOT)

    def test_frozen_benchmark_stays_readable_without_a_model_server(self) -> None:
        fingerprint = model_benchmark.frozen_fingerprint(
            model_benchmark.BENCHMARK,
            model_benchmark.FINGERPRINT,
        )
        cases = model_benchmark.load_cases(model_benchmark.BENCHMARK)

        self.assertEqual(len(fingerprint), 64)
        self.assertGreater(len(cases), 0)


if __name__ == "__main__":
    unittest.main()
