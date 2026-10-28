"""Wspólna baza V1–V4 leży w Treningu, nie w katalogu głównym."""

from __future__ import annotations

import unittest
from pathlib import Path

import trening.train_agency_pilot_base as legacy
from trening import train_agency_pilot_base


class AgencyBaseTrainerMigrationTests(unittest.TestCase):
    def test_legacy_bridge_exports_the_shared_training_base(self) -> None:
        self.assertIs(legacy._validate_chat_template_rows, train_agency_pilot_base._validate_chat_template_rows)
        self.assertIs(legacy._chat_template_ids, train_agency_pilot_base._chat_template_ids)
        self.assertEqual(train_agency_pilot_base.ROOT, Path(__file__).resolve().parents[1])

    def test_v2_v3_v4_use_the_training_base_directly(self) -> None:
        from trening import train_agency_pilot_v2, train_agency_pilot_v3, train_agency_pilot_v4

        self.assertIs(train_agency_pilot_v2.pilot, train_agency_pilot_base)
        self.assertIs(train_agency_pilot_v3.pilot, train_agency_pilot_base)
        self.assertIs(train_agency_pilot_v4.pilot, train_agency_pilot_base)


if __name__ == "__main__":
    unittest.main()
