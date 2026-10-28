"""Syntetyczny zestaw bazowy Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_seed_dataset as legacy
import trening.agency_seed_dataset as training


class AgencySeedDatasetMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_static_source(self) -> None:
        self.assertIs(legacy.examples, training.examples)
        self.assertEqual(legacy.OUTPUT, training.OUTPUT)
        self.assertEqual(legacy.MANIFEST, training.MANIFEST)


if __name__ == "__main__":
    unittest.main()
