from __future__ import annotations

import unittest

from trening.agency_real_situation_test import build_case, interpret_generation, summarize


class RealSituationReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {
            "id": "real_candidate_001",
            "synthetic": False,
            "eligible_for_positive_training": False,
            "incoming_text": "  Are you still here?  ",
            "context": ["PLAYER: hi", "", "PEVA: maybe"],
        }

    def test_case_uses_reactive_contract_and_no_memories(self) -> None:
        case = build_case(self.row)
        self.assertEqual("AGENCY_REACTIVE", case["mode"])
        self.assertEqual("Are you still here?", case["input"])
        self.assertEqual(["PLAYER: hi", "PEVA: maybe"], case["context"])
        self.assertEqual([], case["memories"])

    def test_valid_reply_is_kept(self) -> None:
        result = interpret_generation(build_case(self.row), '{"decision":"REPLY","reason_code":"CASUAL","message":"Yep. You?"}')
        self.assertTrue(result["schema_valid"])
        self.assertFalse(result["fallback_used"])
        self.assertEqual("REPLY", result["decision"]["decision"])

    def test_silent_contract_is_enforced(self) -> None:
        result = interpret_generation(build_case(self.row), '{"decision":"WAIT","reason_code":"NOT_NOW","message":"nope"}')
        self.assertFalse(result["schema_valid"])
        self.assertTrue(result["fallback_used"])
        self.assertEqual("WAIT", result["decision"]["decision"])
        self.assertIsNone(result["decision"]["message"])

    def test_unconfirmed_completed_action_is_flagged(self) -> None:
        result = interpret_generation(build_case(self.row), '{"decision":"REPLY","reason_code":"ACTION","message":"I sent troops."}')
        self.assertFalse(result["action_safe"])

    def test_confirmed_completed_action_is_allowed_for_review(self) -> None:
        row = dict(self.row, context=["CONFIRMED: PEva sent troops."])
        result = interpret_generation(build_case(row), '{"decision":"REPLY","reason_code":"ACTION","message":"I sent troops."}')
        self.assertTrue(result["action_safe"])

    def test_summary_tracks_contract_and_safety(self) -> None:
        valid = interpret_generation(build_case(self.row), '{"decision":"IGNORE","reason_code":"NO_REPLY"}')
        unsafe = interpret_generation(build_case(self.row), '{"decision":"REPLY","reason_code":"ACTION","message":"I attacked them."}')
        report = summarize([valid, unsafe])
        self.assertEqual(2, report["records"])
        self.assertEqual(2, report["schema_valid"])
        self.assertEqual(1, report["unsafe_action_claims"])
        self.assertEqual({"IGNORE": 1, "REPLY": 1}, report["decision_counts"])


if __name__ == "__main__":
    unittest.main()
