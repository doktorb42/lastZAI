from __future__ import annotations

import unittest

from trening.agency_corrective_dataset_v4 import EXPECTED_COUNTS, examples, validate
from trening.agency_dataset_builder_v4 import EXPECTED_TOTAL, build
from trening.train_agency_pilot_v4 import FROZEN_BENCHMARK_SHA256, _frozen_benchmark_status


class AgencyV4DatasetTests(unittest.TestCase):
    def test_focused_social_examples_are_safe_and_reply_weighted(self) -> None:
        report = validate(examples())
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["category_counts"], EXPECTED_COUNTS)
        rows = examples()
        social = [row for row in rows if row["category_group"] == "corrective_v4_social_reciprocity"]
        self.assertEqual(len(social), 36)
        self.assertTrue(all(row["target"]["decision"] in {"REPLY", "ASK_BACK"} for row in social))

    def test_builder_is_family_grouped_and_holds_out_benchmarks(self) -> None:
        report = build(write=False)
        self.assertTrue(report["pilot_gate"]["passed"], report["audit"]["errors"])
        self.assertEqual(report["split"]["train_count"] + report["split"]["validation_count"], EXPECTED_TOTAL)
        self.assertTrue(report["pilot_gate"]["no_frozen_benchmark_overlap"])
        self.assertTrue(report["pilot_gate"]["no_train_validation_leakage"])
        self.assertEqual(report["pilot_gate"]["unsafe_target_claims"], 0)
        self.assertEqual(report["pilot_gate"]["social_reply_examples"], 36)

    def test_preflight_keeps_the_pinned_frozen_benchmark(self) -> None:
        frozen = _frozen_benchmark_status()
        self.assertEqual(frozen["expected_sha256"], FROZEN_BENCHMARK_SHA256)
        self.assertTrue(frozen["matches_expected"])


if __name__ == "__main__":
    unittest.main()
