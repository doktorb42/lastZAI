"""Anonimowe podsumowanie danych treningowych ma jedno źródło."""

from __future__ import annotations

import unittest

import trening.private_message_corpus_summary as legacy
import trening.private_message_corpus_summary as training


class PrivateMessageCorpusSummaryMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_summary_functions_and_paths(self) -> None:
        self.assertIs(legacy.summarize_message_records, training.summarize_message_records)
        self.assertIs(legacy.build_summary, training.build_summary)
        self.assertIs(legacy.write_summary, training.write_summary)
        self.assertEqual(legacy.OUTPUT, training.OUTPUT)
        self.assertTrue(str(training.OUTPUT).endswith("private_message_corpus_summary_v1.json"))


if __name__ == "__main__":
    unittest.main()
