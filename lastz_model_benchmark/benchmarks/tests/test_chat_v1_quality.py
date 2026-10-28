"""No-model regression tests for the Chat V1 benchmark quality gate."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BENCHMARKS_DIR = Path(__file__).resolve().parents[1]
RUNNER_PATH = BENCHMARKS_DIR / "run_performance_benchmark.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("lastz_performance_benchmark_test", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load benchmark runner: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ChatV1QualityGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()
        cls.cases = cls.runner.load_chat_cases()
        cls.by_id = {case["id"]: case for case in cls.cases}

    def test_fixture_files_are_valid_utf8_and_include_social_turns_two_to_five(self) -> None:
        fixture = BENCHMARKS_DIR / "cases" / "chat_v1_quality_cases.jsonl"
        decoded = fixture.read_bytes().decode("utf-8")
        self.assertNotIn("\ufffd", decoded)
        self.assertIn("\U0001f604", decoded)
        social_cases = [case for case in self.cases if case.get("chat_v1_quality_case")]
        self.assertEqual(
            [case["conversation_turn_count"] for case in social_cases if case.get("continuity_test")],
            [2, 3, 4, 5],
        )
        for case in social_cases:
            with self.subTest(case_id=case["id"]):
                self.assertTrue(case.get("canonical_task_chat"))
                self.assertEqual(case["messages"], self.runner.render_chat_messages(case["canonical_context"]))
                self.assertEqual(case["task_input"], self.runner.build_task_chat_input(case["canonical_context"]))

    def test_chat_v1_payload_is_exact_canonical_v2_renderer_output(self) -> None:
        case = self.by_id["chat_v1_social_how_are_you_turn2"]
        payload = self.runner.chat_payload(case, SimpleNamespace(chat_temperature=0.35), stream=True)
        self.assertEqual(payload["messages"], self.runner.render_chat_messages(case["canonical_context"]))
        self.assertEqual(payload["messages"][0]["content"], self.runner.render_chat_messages(case["canonical_context"])[0]["content"])
        self.assertIn("/no_think", payload["messages"][0]["content"])

    def test_deterministic_gate_vectors_cover_observed_regressions(self) -> None:
        contract = self.runner.run_chat_v1_quality_gate_contract(self.cases)
        self.assertTrue(contract["all_passed"], contract["vectors"])
        self.assertEqual(contract["passed"], contract["total"])
        all_categories = {
            category
            for record in contract["vectors"]
            for category in record["actual_failure_categories"]
        }
        self.assertTrue({
            "generic_assistant_tone",
            "support_agent_language",
            "self_identifies_as_program",
            "self_identifies_as_ai",
            "denies_normal_persona",
            "unnatural_followup",
            "conversation_reset",
            "excessive_formality",
            "empty_reply_when_reply_expected",
        } <= all_categories)

    def test_plain_certainly_is_excessively_formal(self) -> None:
        quality = self.runner.score_chat_output("Certainly!", self.by_id["chat_v1_social_hello"])
        self.assertFalse(quality["quality_pass"])
        self.assertEqual(quality["score"], 0.0)
        self.assertIn("excessive_formality", quality["failure_categories"])

    def test_good_social_reply_does_not_require_slang_or_emoji(self) -> None:
        quality = self.runner.score_chat_output("good, you?", self.by_id["chat_v1_social_how_are_you_turn2"])
        self.assertTrue(quality["quality_pass"], quality)
        self.assertTrue(quality["assistant_like_tone_pass"])
        self.assertTrue(quality["social_reply"]["continuity_pass"])

    def test_empty_reply_only_fails_when_case_expects_a_reply(self) -> None:
        # Greeting, direct question, social follow-up, and coordination all
        # explicitly expect a reply in the benchmark fixtures.
        for case_id in (
            "chat_v1_social_hello",
            "chat_v1_social_how_are_you_turn2",
            "chat_v1_social_im_fine_turn4",
            "chat_v1_direct_coordination_request",
        ):
            with self.subTest(case_id=case_id):
                expected = self.runner.score_chat_output("", self.by_id[case_id])
                self.assertFalse(expected["quality_pass"])
                self.assertIn("empty_reply_when_reply_expected", expected["failure_categories"])

        optional_case = dict(self.by_id["chat_v1_social_hello"])
        optional_case.update({
            "reply_expected": False,
            "target_words": [0, 0],
            "min_sentences": 0,
            "social_expectations": None,
        })
        optional = self.runner.score_chat_output("", optional_case)
        self.assertTrue(optional["quality_pass"], optional)
        self.assertNotIn("empty_reply_when_reply_expected", optional["failure_categories"])

    def test_summary_exposes_chat_v1_failure_counters(self) -> None:
        bad = self.runner.score_chat_output(
            "I'm just a program. How can I assist you today?",
            self.by_id["chat_v1_social_how_are_you_turn2"],
        )
        good = self.runner.score_chat_output(
            "doing good haha, you?",
            self.by_id["chat_v1_social_how_are_you_turn2"],
        )
        records = [
            {
                "streaming": True,
                "chat_v1_quality_case": True,
                "continuity_test": True,
                "quality": quality,
                "e2e_ms": 10.0,
                "ttfb_ms": 3.0,
                "ttft_ms": 4.0,
                "prompt_tokens_per_second": 100.0,
                "generation_tokens_per_second": 20.0,
                "error": None,
            }
            for quality in (bad, good)
        ]
        summary = self.runner.summarize_chat(records[0], records, ["streaming"])
        counters = summary["warm"]["streaming"]["chat_v1_quality"]
        self.assertEqual(counters["requests"], 2)
        self.assertEqual(counters["assistant_like_tone_failure_count"], 1)
        self.assertEqual(counters["continuity_failure_count"], 1)
        self.assertGreaterEqual(counters["failure_category_counts"]["self_identifies_as_program"], 1)


if __name__ == "__main__":
    unittest.main()
