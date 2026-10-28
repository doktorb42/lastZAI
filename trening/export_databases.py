"""Explicit, read-only export of selected PEva SQLite databases.

Importing this module never reads or writes a player database. A caller must
explicitly invoke :func:`export_databases` or run this file as a command.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_DIR = ROOT / "data" / "databases"
DEFAULT_OUTPUT = ROOT / "data" / "exports" / "peva_db_dump.json"
DATABASE_NAMES = (
    "conversation.db",
    "feedback.db",
    "humanization.db",
    "memory_review.db",
)


def _read_database(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read user tables through SQLite's read-only mode."""

    uri = path.resolve().as_uri() + "?mode=ro"
    database = sqlite3.connect(uri, uri=True)
    try:
        database.row_factory = sqlite3.Row
        tables = [
            str(row["name"])
            for row in database.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        result: dict[str, list[dict[str, Any]]] = {}
        for table in tables:
            quoted_name = table.replace('"', '""')
            rows = database.execute(f'SELECT * FROM "{quoted_name}"').fetchall()
            result[table] = [dict(row) for row in rows]
        return result
    finally:
        # ``sqlite3.Connection`` as a context manager commits or rolls back,
        # but does not close the file handle. Close it explicitly so an export
        # never leaves a Windows database locked.
        database.close()


def export_databases(
    *,
    database_dir: Path = DEFAULT_DATABASE_DIR,
    output_path: Path = DEFAULT_OUTPUT,
    database_names: Iterable[str] = DATABASE_NAMES,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Export existing selected databases after an explicit caller request."""

    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for name in database_names:
        path = Path(database_dir) / str(name)
        if path.is_file():
            result[path.name] = _read_database(path)

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return result


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit read-only PEva database export.")
    parser.add_argument("--database-dir", default=str(DEFAULT_DATABASE_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    output = _project_path(args.output)
    data = export_databases(
        database_dir=_project_path(args.database_dir),
        output_path=output,
    )
    print(f"[OK] Saved {len(data)} database export(s): {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
