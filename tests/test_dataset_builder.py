from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trening.dataset_builder import DatasetBuilder
from trening.feedback_store import FeedbackRating, FeedbackStore


class DatasetBuilderTests(unittest.TestCase):
    def test_feedback_filters_and_edits_raw_private_samples_without_room_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            rows = [
                {
                    "message_key": "m-good", "room_id": "custom_100_200_v2", "player_uid": "100", "channel": "custom",
                    "input_message": "hello", "recent_context": [], "intent": "GREETING", "decision": "REPLY", "reply": "hey",
                },
                {
                    "message_key": "m-edit", "room_id": "custom_100_200_v2", "player_uid": "100", "channel": "custom",
                    "input_message": "you coming?", "recent_context": ["PLAYER: hello"], "intent": "QUESTION", "decision": "REPLY", "reply": "certainly I will assist",
                },
                {
                    "message_key": "m-bad", "room_id": "custom_101_200_v2", "player_uid": "101", "channel": "custom",
                    "input_message": "reinforce me", "recent_context": [], "intent": "ACTION_REQUEST", "decision": "REPLY", "reply": "done",
                },
                {
                    "message_key": "m-unsafe", "room_id": "custom_102_200_v2", "player_uid": "102", "channel": "custom",
                    "input_message": "you did it?", "recent_context": [], "intent": "QUESTION", "decision": "REPLY",
                    "command_status": "DETECTED", "reply": "I'll send reinforcements.",
                },
                {
                    "message_key": "m-implicit", "room_id": "custom_103_200_v2", "player_uid": "103", "channel": "custom",
                    "input_message": "reinforce me", "recent_context": [], "intent": "ACTION_REQUEST", "decision": "REPLY",
                    "command_status": "DETECTED", "reply": "Sure thing!",
                },
                {
                    "message_key": "m-rewrite", "room_id": "custom_100_200_v2", "player_uid": "100", "channel": "custom",
                    "input_message": "reinforce me", "recent_context": [], "intent": "ACTION_REQUEST", "decision": "REPLY",
                    "command_status": "DETECTED", "reply": "lemme check.", "reply_original": "done",
                    "reply_guard_triggered": True, "reply_rewritten": True, "reply_rewrite_source": "llm_safe_rewrite",
                },
                {
                    "message_key": "m-fallback", "room_id": "custom_100_200_v2", "player_uid": "100", "channel": "custom",
                    "input_message": "attack him", "recent_context": [], "intent": "ACTION_COMMAND", "decision": "REPLY",
                    "command_status": "DETECTED", "reply": "not yet.", "reply_original": "on it",
                    "reply_guard_triggered": True, "reply_rewritten": True, "reply_rewrite_source": "deterministic_fallback",
                },
                {
                    "message_key": "m-future", "room_id": "custom_100_200_v2", "player_uid": "100", "channel": "custom",
                    "input_message": "join my rally", "recent_context": [], "intent": "ACTION_COMMAND", "decision": "REPLY",
                    "command_status": "DETECTED", "reply": "Sure, I'll join your rally.",
                },
            ]
            (raw / "private.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            feedback = FeedbackStore(root / "feedback.db")
            try:
                good = feedback.record(message_key="m-good", player_uid="100", model_reply="hey")
                feedback.rate(good.feedback_id, FeedbackRating.GOOD)
                edited = feedback.record(message_key="m-edit", player_uid="100", model_reply="certainly I will assist")
                feedback.edit(edited.feedback_id, "yeah, I'll check")
                bad = feedback.record(message_key="m-bad", player_uid="101", model_reply="done")
                feedback.rate(bad.feedback_id, FeedbackRating.BAD)
                rewrite_feedback = feedback.record(message_key="m-rewrite", player_uid="100", model_reply="lemme check.")
                feedback.rate(rewrite_feedback.feedback_id, FeedbackRating.GOOD)
                builder = DatasetBuilder(raw_dir=raw, processed_dir=root / "processed", feedback_store=feedback)
                samples = builder.build()
                self.assertEqual(len(samples), 3)
                self.assertIn("yeah, I'll check", [sample.expected_reply for sample in samples])
                rewrite = next(sample for sample in samples if sample.sample_id == "m-rewrite")
                self.assertEqual(rewrite.reply_rewrite_source, "llm_safe_rewrite")
                self.assertNotIn("done", json.dumps(rewrite.chatml()))
                counts = builder.export(samples, split=True)
                self.assertEqual(counts["all"], 3)
                processed = root / "processed"
                self.assertTrue((processed / "peva_private_v1.jsonl").is_file())
                train = (processed / "peva_reply_train.jsonl").read_text(encoding="utf-8")
                validation = (processed / "peva_reply_validation.jsonl").read_text(encoding="utf-8")
                # Both records share room-a, so their group never leaks across splits.
                self.assertFalse(train.strip() and validation.strip())
            finally:
                feedback.close()


if __name__ == "__main__":
    unittest.main()
