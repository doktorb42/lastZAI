"""Ręczne Review Studio Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_review_studio as legacy
import trening.agency_review_studio as training


class AgencyReviewStudioMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_manual_review_types(self) -> None:
        self.assertIs(legacy.ReviewStudio, training.ReviewStudio)
        self.assertIs(legacy.ReviewError, training.ReviewError)
        self.assertIs(legacy.serve, training.serve)
        self.assertEqual(legacy.DEFAULT_STORE, training.DEFAULT_STORE)


if __name__ == "__main__":
    unittest.main()
