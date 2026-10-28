"""Konserwatywne czyszczenie Agency ma źródło w Treningu i mostek ścieżek."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import trening.agency_cleanup as legacy
import trening.agency_cleanup as training


class AgencyCleanupMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_cleanup_functions(self) -> None:
        self.assertIs(legacy.inspect_and_apply, training.inspect_and_apply)
        self.assertIs(legacy.backup_info, training.backup_info)

    def test_legacy_temporary_database_path_is_forwarded_to_training_source(self) -> None:
        original = training.DATABASES
        try:
            with tempfile.TemporaryDirectory() as temporary:
                replacement = Path(temporary) / "databases"
                legacy.DATABASES = replacement
                self.assertEqual(training.DATABASES, replacement)
        finally:
            legacy.DATABASES = original


if __name__ == "__main__":
    unittest.main()
