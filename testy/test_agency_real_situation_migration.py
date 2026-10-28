"""Ręczny test realnych sytuacji Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_real_situation_test as legacy
import trening.agency_real_situation_test as training


class AgencyRealSituationMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_review_only_functions(self) -> None:
        self.assertIs(legacy.build_case, training.build_case)
        self.assertIs(legacy.interpret_generation, training.interpret_generation)
        self.assertEqual(legacy.REAL_CANDIDATES, training.REAL_CANDIDATES)


if __name__ == "__main__":
    unittest.main()
