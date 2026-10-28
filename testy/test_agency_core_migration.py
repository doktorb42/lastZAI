"""Historyczny rdzeń Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_core as legacy
import trening.agency_core as training


class AgencyCoreMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_core(self) -> None:
        self.assertIs(legacy.AgencyCore, training.AgencyCore)

    def test_missing_completion_falls_back_to_wait(self) -> None:
        result = training.AgencyCore().decide_reactive({})
        self.assertEqual(result.decision, "WAIT")
        self.assertIsNone(result.message)


if __name__ == "__main__":
    unittest.main()
