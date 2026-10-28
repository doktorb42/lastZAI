"""Regression checks for the unique-context PEva voice corrective source."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_peva_voice_contextual import build_rows  # noqa: E402
from chat_v1_contract import canonical_json, validate_source_record  # noqa: E402


class ContextualVoiceBuilderTests(unittest.TestCase):
    def test_source_is_large_enough_and_every_prompt_has_one_target(self) -> None:
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

    def test_every_record_stays_inside_the_canonical_contract(self) -> None:
        for row in build_rows():
            with self.subTest(record_id=row["record_id"]):
                normalized, errors, _details = validate_source_record(row, require_approved=True)
                self.assertIsNotNone(normalized)
                self.assertEqual(errors, [])
