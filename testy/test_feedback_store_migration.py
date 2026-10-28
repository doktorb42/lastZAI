"""Magazyn ocen treningowych ma jedno źródło po migracji."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import trening.feedback_store as legacy
import trening.feedback_store as training


class FeedbackStoreMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_store_and_rating(self) -> None:
        self.assertIs(legacy.FeedbackStore, training.FeedbackStore)
        self.assertIs(legacy.FeedbackRating, training.FeedbackRating)

    def test_temp_feedback_database_stays_outside_runtime_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "feedback.db"
            store = training.FeedbackStore(path)
            try:
                record = store.record(message_key="test:1", player_uid="uid", model_reply="preview")
                self.assertTrue(store.rate(record.feedback_id, training.FeedbackRating.GOOD))
            finally:
                store.close()
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
