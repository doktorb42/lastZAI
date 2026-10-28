"""Regression tests for the evidence-driven PEva social-repair source."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_peva_voice_social_repair import build_rows  # noqa: E402
from chat_v1_contract import canonical_json, validate_source_record  # noqa: E402


class SocialRepairVoiceBuilderTests(unittest.TestCase):
    def test_source_has_unique_contexts_and_required_breadth(self) -> None:
        rows = build_rows()
        self.assertEqual(len(rows), 330)
        prompts = [canonical_json(row["task_snapshot"]["input"]) for row in rows]
        self.assertEqual(len(prompts), len(set(prompts)))
        self.assertEqual({row["category"] for row in rows}, {
            "casual_greeting", "casual_conversation", "alliance_coordination", "event_discussion",
            "strategy_game", "scheduling", "humor_teasing", "short_reaction",
            "disagreement_conflict", "clarification", "unknown_information",
        })
        self.assertTrue(all(row["source_type"] == "synthetic_candidate" for row in rows))

    def test_source_excludes_observed_bad_social_shortcuts(self) -> None:
        for row in build_rows():
            with self.subTest(record_id=row["record_id"]):
                reply = str(row["approved_reply"]).lower()
                self.assertNotIn("i am not interested", reply)
                self.assertNotIn("i will check", reply)
                self.assertNotIn("i will eat", reply)
                self.assertTrue(reply.endswith((".", "?", "!")))
                normalized, errors, _details = validate_source_record(row, require_approved=True)
                self.assertIsNotNone(normalized)
                self.assertEqual(errors, [])
