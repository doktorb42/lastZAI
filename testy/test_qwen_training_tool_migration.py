"""Narzędzia danych i Qwen należą do Treningu, nie do zwykłego startu."""

from __future__ import annotations

import unittest
from pathlib import Path

import trening.train_qwen7b as legacy_runner
import trening.training_dataset_audit as legacy_audit
from trening import train_qwen7b, training_dataset_audit


ROOT = Path(__file__).resolve().parents[1]


class QwenTrainingToolMigrationTests(unittest.TestCase):
    def test_audit_bridge_exports_the_training_implementation(self) -> None:
        self.assertIs(legacy_audit.audit, training_dataset_audit.audit)
        self.assertIs(legacy_audit.main, training_dataset_audit.main)
        self.assertEqual(training_dataset_audit.ROOT, ROOT)

    def test_runner_bridge_exports_the_training_implementation(self) -> None:
        self.assertIs(legacy_runner.preflight, train_qwen7b.preflight)
        self.assertIs(legacy_runner.main, train_qwen7b.main)
        self.assertEqual(train_qwen7b.ROOT, ROOT)

    def test_audit_remains_read_only_at_import_time(self) -> None:
        report = training_dataset_audit.audit()

        self.assertEqual(report["scope"], {
            "training": False,
            "model_loading": False,
            "network": False,
            "data_mutation": False,
        })


if __name__ == "__main__":
    unittest.main()
