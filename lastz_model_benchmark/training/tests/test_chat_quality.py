"""Focused deterministic tests for the Chat V1 player-ready quality gate."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


TRAINING_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = TRAINING_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from chat_quality import (  # noqa: E402
    ASSISTANT_LIKE_FAILURE_CATEGORIES,
    summarize_chat_quality_verdicts,
    validate_player_ready_output,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chat_v1_quality_cases.json"


class ChatV1QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_observed_and_pattern_family_failures_are_hard_gated(self) -> None:
        for case in self.fixtures["failure_cases"]:
            with self.subTest(case=case["id"]):
                verdict = validate_player_ready_output(case["candidate"], case["source_record"])
                self.assertFalse(verdict["valid"])
                self.assertFalse(verdict["quality_pass"])
                if case["id"] == "empty_reply_expected":
                    self.assertTrue(verdict["assistant_like_tone_pass"])
                else:
                    self.assertFalse(verdict["assistant_like_tone_pass"])
                self.assertEqual(verdict["quality_score"], 0)
                self.assertTrue(set(case["expected_categories"]).issubset(verdict["errors"]))

    def test_natural_player_replies_pass_without_forced_slang(self) -> None:
        for case in self.fixtures["natural_pass_cases"]:
            with self.subTest(case=case["id"]):
                verdict = validate_player_ready_output(case["candidate"], case["source_record"])
                self.assertTrue(verdict["valid"], verdict)
                self.assertTrue(verdict["assistant_like_tone_pass"], verdict)
                self.assertEqual(verdict["assistant_like_tone_score"], 0)

    def test_multiturn_continuity_and_short_social_length_cases(self) -> None:
        for case in self.fixtures["continuity_cases"]:
            with self.subTest(case=case["id"]):
                verdict = validate_player_ready_output(case["candidate"], case["source_record"])
                expected_categories = set(case.get("expected_categories", []))
                expected_errors = set(case.get("expected_errors", []))
                if expected_categories or expected_errors:
                    self.assertFalse(verdict["valid"], verdict)
                else:
                    self.assertTrue(verdict["valid"], verdict)
                self.assertTrue(expected_categories.issubset(verdict["errors"]))
                self.assertTrue(expected_errors.issubset(verdict["errors"]))

    def test_empty_reply_can_only_be_allowed_in_an_explicit_no_reply_context(self) -> None:
        expected = validate_player_ready_output("", {"reply_expected": True})
        allowed = validate_player_ready_output(None, {"reply_expected": False})
        self.assertIn("empty_reply_when_reply_expected", expected["errors"])
        self.assertFalse(expected["valid"])
        self.assertTrue(allowed["valid"], allowed)
        self.assertTrue(allowed["empty_reply_allowed"])

    def test_curly_apostrophes_are_normalized_or_rejected_by_policy(self) -> None:
        normalized = validate_player_ready_output("I’m good, you?", {"reply_expected": True})
        rejected = validate_player_ready_output(
            "I’m good, you?", {"reply_expected": True}, apostrophe_policy="reject"
        )
        self.assertTrue(normalized["valid"], normalized)
        self.assertEqual(normalized["normalized_text"], "I'm good, you?")
        self.assertIn("curly_apostrophes_normalized_to_ascii", normalized["warnings"])
        self.assertFalse(rejected["valid"])
        self.assertIn("curly_apostrophe_not_allowed", rejected["errors"])

    def test_reply_wrapper_is_visible_prompt_scaffolding_not_player_chat(self) -> None:
        verdict = validate_player_ready_output(
            'Sure, here\'s my reply: "No rush, I can wait."',
            {"reply_expected": True},
            apostrophe_policy="reject",
        )
        self.assertFalse(verdict["valid"], verdict)
        self.assertIn("visible_meta_reasoning_or_prompt_analysis", verdict["errors"])

    def test_report_counters_keep_the_failure_taxonomy_visible(self) -> None:
        verdicts = [
            validate_player_ready_output(case["candidate"], case["source_record"])
            for case in self.fixtures["failure_cases"] + self.fixtures["continuity_cases"] + self.fixtures["natural_pass_cases"]
        ]
        report = summarize_chat_quality_verdicts(verdicts)
        self.assertEqual(report["total"], len(verdicts))
        self.assertGreater(report["assistant_like_tone_failure_count"], 0)
        self.assertEqual(report["empty_reply_when_reply_expected_count"], 1)
        for category in ASSISTANT_LIKE_FAILURE_CATEGORIES:
            self.assertIn(category, report["assistant_like_tone_category_counts"] | {"empty_reply_when_reply_expected": 1})


if __name__ == "__main__":
    unittest.main()
