from __future__ import annotations

import unittest

from trening.benchmark_evaluator import load_cases, run_private_benchmark


class BenchmarkEvaluatorTests(unittest.TestCase):
    def test_private_benchmark_has_over_fifty_cases_and_zero_unsafe_stub_replies(self) -> None:
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 50)
        result = run_private_benchmark()
        self.assertEqual(result["cases"], len(cases))
        self.assertEqual(result["metrics"]["unsafe_action_claim_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
