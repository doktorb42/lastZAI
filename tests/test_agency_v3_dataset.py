from __future__ import annotations

import unittest

from trening.agency_corrective_dataset_v3 import EXPECTED_COUNTS, examples, validate
from trening.agency_dataset_builder_v3 import build
from trening.train_agency_pilot_v3 import FROZEN_BENCHMARK_SHA256, _frozen_benchmark_status


class AgencyV3DatasetTests(unittest.TestCase):
    def test_social_corrective_data_has_contracts_and_balanced_contrasts(self) -> None:
        report = validate(examples())
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["count"], 30)
        self.assertEqual(report["category_counts"], EXPECTED_COUNTS)
        rows = examples()
        positive = sum(row["target"]["decision"] in {"REPLY", "ASK_BACK", "CHANGE_TOPIC"} for row in rows)
        silent = sum(row["target"]["decision"] in {"IGNORE", "WAIT"} for row in rows)
        self.assertEqual((positive, silent), (27, 3))

    def test_builder_keeps_bench_held_out_and_family_grouped(self) -> None:
        report = build(write=False)
        self.assertTrue(report["audit"]["gates"]["passed"], report["audit"]["errors"])
        self.assertEqual(report["sources"]["corrective_v3_count"], 30)
        self.assertEqual(report["split"]["train_count"] + report["split"]["validation_count"], 264)
        self.assertTrue(report["audit"]["gates"]["no_frozen_benchmark_overlap"])
        self.assertTrue(report["audit"]["gates"]["no_train_validation_leakage"])
        self.assertEqual(report["audit"]["gates"]["unsafe_target_claims"], 0)
        self.assertEqual(report["audit"]["gates"]["invalid_proactive_uid"], 0)

    def test_preflight_uses_unchanged_frozen_benchmark(self) -> None:
        frozen = _frozen_benchmark_status()
        self.assertEqual(frozen["expected_sha256"], FROZEN_BENCHMARK_SHA256)
        self.assertTrue(frozen["matches_expected"])


if __name__ == "__main__":
    unittest.main()
