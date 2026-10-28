"""Human feedback storage for reviewable PEva training candidates."""

from __future__ import annotations

import argparse
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from config.runtime_config import DATA_DIR


class FeedbackRating(str, Enum):
    GOOD = "GOOD"
    BAD = "BAD"
    EDITED = "EDITED"


@dataclass(slots=True)
class FeedbackRecord:
    feedback_id: str
    timestamp: float
    message_key: str
    player_uid: str
    model_reply: str
    intent: str | None = None
    decision: str | None = None
    command: str | None = None
    rating: FeedbackRating | None = None
    corrected_reply: str | None = None
    notes: str | None = None
    original_reply: str | None = None
    rewrite_source: str | None = None
    guard_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rating"] = self.rating.value if self.rating else None
        return data


class FeedbackStore:
    def __init__(self, path: str | Path = DATA_DIR / "databases" / "feedback.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reply_feedback (
                feedback_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                message_key TEXT NOT NULL,
                player_uid TEXT NOT NULL,
                model_reply TEXT NOT NULL,
                intent TEXT,
                decision TEXT,
                command TEXT,
                rating TEXT,
                corrected_reply TEXT,
                notes TEXT
            );
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_message_key ON reply_feedback(message_key, timestamp);")
        self._ensure_column("original_reply", "TEXT")
        self._ensure_column("rewrite_source", "TEXT")
        self._ensure_column("guard_reason", "TEXT")
        self.conn.commit()

    def _ensure_column(self, name: str, sql_type: str) -> None:
        columns = {str(row[1]) for row in self.conn.execute("PRAGMA table_info(reply_feedback);")}
        if name not in columns:
            self.conn.execute(f"ALTER TABLE reply_feedback ADD COLUMN {name} {sql_type};")

    def close(self) -> None:
        self.conn.close()

    def record(
        self,
        *,
        message_key: str,
        player_uid: str,
        model_reply: str,
        intent: str | None = None,
        decision: str | None = None,
        command: str | None = None,
        notes: str | None = None,
        original_reply: str | None = None,
        rewrite_source: str | None = None,
        guard_reason: str | None = None,
    ) -> FeedbackRecord:
        record = FeedbackRecord(
            feedback_id=uuid.uuid4().hex,
            timestamp=time.time(),
            message_key=str(message_key),
            player_uid=str(player_uid),
            model_reply=str(model_reply),
            intent=intent,
            decision=decision,
            command=command,
            notes=notes,
            original_reply=original_reply,
            rewrite_source=rewrite_source,
            guard_reason=guard_reason,
        )
        self.conn.execute(
            """
            INSERT INTO reply_feedback (
                feedback_id, timestamp, message_key, player_uid, model_reply,
                intent, decision, command, rating, corrected_reply, notes,
                original_reply, rewrite_source, guard_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                record.feedback_id, record.timestamp, record.message_key,
                record.player_uid, record.model_reply, record.intent,
                record.decision, record.command, None, None, record.notes,
                record.original_reply, record.rewrite_source, record.guard_reason,
            ),
        )
        self.conn.commit()
        return record

    def list_recent(self, limit: int = 20) -> list[FeedbackRecord]:
        rows = self.conn.execute(
            "SELECT * FROM reply_feedback ORDER BY timestamp DESC LIMIT ?;", (max(1, int(limit)),)
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def rate(self, feedback_id: str, rating: FeedbackRating | str) -> bool:
        value = FeedbackRating(str(getattr(rating, "value", rating)).upper())
        cursor = self.conn.execute(
            "UPDATE reply_feedback SET rating = ? WHERE feedback_id = ?;",
            (value.value, str(feedback_id)),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def edit(self, feedback_id: str, corrected_reply: str) -> bool:
        text = " ".join(str(corrected_reply or "").split()).strip()
        if not text:
            raise ValueError("corrected_reply cannot be empty")
        cursor = self.conn.execute(
            "UPDATE reply_feedback SET rating = 'EDITED', corrected_reply = ? WHERE feedback_id = ?;",
            (text, str(feedback_id)),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def by_message_keys(self, message_keys: set[str]) -> dict[str, FeedbackRecord]:
        if not message_keys:
            return {}
        placeholders = ",".join("?" for _ in message_keys)
        rows = self.conn.execute(
            f"SELECT * FROM reply_feedback WHERE message_key IN ({placeholders}) ORDER BY timestamp DESC;",
            tuple(message_keys),
        ).fetchall()
        result: dict[str, FeedbackRecord] = {}
        for row in rows:
            record = _row_to_record(row)
            result.setdefault(record.message_key, record)
        return result


def _row_to_record(row: sqlite3.Row) -> FeedbackRecord:
    rating = row["rating"]
    return FeedbackRecord(
        feedback_id=row["feedback_id"], timestamp=float(row["timestamp"]),
        message_key=row["message_key"], player_uid=row["player_uid"],
        model_reply=row["model_reply"], intent=row["intent"], decision=row["decision"],
        command=row["command"], rating=FeedbackRating(rating) if rating else None,
        corrected_reply=row["corrected_reply"], notes=row["notes"],
        original_reply=row["original_reply"], rewrite_source=row["rewrite_source"],
        guard_reason=row["guard_reason"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Review PEva reply candidates.")
    parser.add_argument("--list-recent", action="store_true")
    parser.add_argument("--rate", nargs=2, metavar=("ID", "good|bad"))
    parser.add_argument("--edit", nargs=2, metavar=("ID", "REPLY"))
    args = parser.parse_args()
    store = FeedbackStore()
    try:
        if args.list_recent:
            for record in store.list_recent():
                print(record.to_dict())
        elif args.rate:
            print("updated" if store.rate(args.rate[0], args.rate[1]) else "not found")
        elif args.edit:
            print("updated" if store.edit(args.edit[0], args.edit[1]) else "not found")
        else:
            parser.print_help()
    finally:
        store.close()


if __name__ == "__main__":
    main()
