"""Detekcja i przegląd pamięci są skupione w Mózgu."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import mozg.memory_candidates as legacy_candidates
import mozg.memory_review as legacy_review
from mozg import memory_candidates as active_candidates
from mozg import memory_review as active_review


class MemoryReviewCompatibilityTests(unittest.TestCase):
    def test_old_and_new_paths_share_memory_review_types(self) -> None:
        self.assertIs(legacy_candidates.MemoryCandidateDetector, active_candidates.MemoryCandidateDetector)
        self.assertIs(legacy_review.MemoryReviewStore, active_review.MemoryReviewStore)

    def test_review_store_uses_explicit_temporary_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = active_review.MemoryReviewStore(Path(temporary) / "review.db")
            try:
                candidate = store.ingest(
                    player_uid="test-player",
                    memory_type="identity",
                    content="my name is Test",
                    confidence=0.98,
                    source_message_key="room:1",
                )
                self.assertEqual(candidate.status, active_review.MemoryReviewStatus.PROMOTED)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
