from __future__ import annotations

import unittest

from trening.agency_corrective_dataset_v2 import EXPECTED_COUNTS, examples, validate
from trening.agency_dataset_builder_v2 import build
from trening.train_agency_pilot_v2 import FROZEN_BENCHMARK_SHA256, _frozen_benchmark_status


class AgencyV2DatasetTests(unittest.TestCase):
    def test_corrective_v2_has_exact_targeted_distribution_and_contracts(self) -> None:
        report = validate(examples())
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["count"], 80)
        self.assertEqual(report["category_counts"], EXPECTED_COUNTS)

    def test_reviewed_v2_builder_is_held_out_and_family_grouped(self) -> None:
        report = build(write=False)
        self.assertTrue(report["audit"]["gates"]["passed"], report["audit"]["errors"])
        self.assertEqual(report["review_coverage"]["reviewed_count"], 160)
        self.assertEqual(report["review_coverage"]["unreviewed_count"], 0)
        self.assertEqual(report["sources"]["corrective_v2_count"], 80)
        self.assertEqual(report["split"]["train_count"] + report["split"]["validation_count"], 234)
        self.assertTrue(report["audit"]["gates"]["no_frozen_benchmark_overlap"])
        self.assertTrue(report["audit"]["gates"]["no_train_validation_leakage"])
        self.assertEqual(report["audit"]["gates"]["unsafe_target_claims"], 0)
        self.assertEqual(report["audit"]["gates"]["invalid_proactive_uid"], 0)

    def test_v2_preflight_uses_the_pinned_frozen_benchmark(self) -> None:
        frozen = _frozen_benchmark_status()
        self.assertEqual(frozen["expected_sha256"], FROZEN_BENCHMARK_SHA256)
        self.assertTrue(frozen["matches_expected"])


if __name__ == "__main__":
    unittest.main()
