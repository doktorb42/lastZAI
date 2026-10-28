from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mozg.memory_candidates import MemoryCandidateDetector
from mozg.memory_review import MemoryReviewStatus, MemoryReviewStore


class _FakeConversationStore:
    def __init__(self) -> None:
        self.memories: list[dict[str, object]] = []

    def add_memory(self, **kwargs: object) -> int:
        self.memories.append(dict(kwargs))
        return len(self.memories)


class MemoryReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = MemoryCandidateDetector()

    def test_explicit_identity_is_auto_promoted_only_at_high_confidence(self) -> None:
        candidate = self.detector.detect("my name is Alex")
        self.assertIsNotNone(candidate)
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeConversationStore()
            store = MemoryReviewStore(Path(tmp) / "review.db")
            try:
                review = store.ingest(
                    player_uid="p1", source_message_key="p1:1",
                    conversation_store=fake, **candidate.to_dict(),
                )
                self.assertEqual(review.status, MemoryReviewStatus.PROMOTED)
                self.assertEqual(len(fake.memories), 1)
            finally:
                store.close()

    def test_preference_and_schedule_remain_reviewable(self) -> None:
        preference = self.detector.detect("I prefer riders")
        schedule = self.detector.detect("I work tomorrow morning")
        self.assertIsNotNone(preference)
        self.assertIsNotNone(schedule)
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeConversationStore()
            store = MemoryReviewStore(Path(tmp) / "review.db")
            try:
                pref_review = store.ingest(player_uid="p1", source_message_key="p1:2", conversation_store=fake, **preference.to_dict())
                schedule_review = store.ingest(player_uid="p1", source_message_key="p1:3", conversation_store=fake, **schedule.to_dict())
                self.assertEqual(pref_review.status, MemoryReviewStatus.PENDING)
                self.assertEqual(schedule_review.status, MemoryReviewStatus.PENDING)
                self.assertIsNotNone(schedule_review.expires_at)
                self.assertEqual(fake.memories, [])
            finally:
                store.close()

    def test_temporary_wait_message_never_becomes_memory(self) -> None:
        self.assertIsNone(self.detector.detect("wait 2 min"))


if __name__ == "__main__":
    unittest.main()
