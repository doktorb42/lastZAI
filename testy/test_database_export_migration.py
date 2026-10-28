"""Eksport baz pozostaje świadomy i wykonywany tylko na wskazanej kopii."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import trening.export_databases as legacy
from trening import export_databases


class DatabaseExportMigrationTests(unittest.TestCase):
    def test_legacy_bridge_exports_the_training_implementation(self) -> None:
        self.assertIs(legacy.export_databases, export_databases.export_databases)
        self.assertIs(legacy.main, export_databases.main)

    def test_export_reads_a_selected_temporary_database_and_writes_only_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database_dir = root / "databases"
            database_dir.mkdir()
            database_path = database_dir / "feedback.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("CREATE TABLE note (id INTEGER PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO note (value) VALUES (?)", ("safe",))
                connection.commit()
            finally:
                connection.close()
            output = root / "exports" / "dump.json"

            result = export_databases.export_databases(
                database_dir=database_dir,
                output_path=output,
                database_names=("feedback.db", "missing.db"),
            )

            self.assertEqual(result, {"feedback.db": {"note": [{"id": 1, "value": "safe"}]}})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), result)
            self.assertTrue(database_path.is_file())


if __name__ == "__main__":
    unittest.main()
