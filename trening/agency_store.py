"""Additive, reviewable persistence for Agency decisions and PEva self memory."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from config.runtime_config import DATA_DIR


class AgencyStore:
    def __init__(self, path: str | Path = DATA_DIR / "databases" / "humanization.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL;")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> "AgencyStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _migrate(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS peva_self_memory (
            memory_id TEXT PRIMARY KEY,
            memory_kind TEXT NOT NULL CHECK(memory_kind IN ('CORE_SELF', 'SELF_REFLECTION')),
            content TEXT NOT NULL,
            provenance TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'PENDING',
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agency_decisions (
            decision_id TEXT PRIMARY KEY,
            goal_id TEXT,
            player_uid TEXT,
            room_id TEXT,
            incoming_text TEXT,
            decision TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            message TEXT,
            status TEXT NOT NULL,
            provenance TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agency_proactive_decisions (
            decision_id TEXT PRIMARY KEY,
            goal_id TEXT,
            target_uid TEXT,
            room_id TEXT,
            decision TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            message TEXT,
            candidate_uids_json TEXT NOT NULL,
            status TEXT NOT NULL,
            provenance TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agency_feedback (
            feedback_id TEXT PRIMARY KEY,
            agency_decision_id TEXT NOT NULL,
            rating TEXT NOT NULL CHECK(rating IN ('GOOD', 'BAD', 'EDITED', 'SKIP')),
            corrected_decision TEXT,
            corrected_message TEXT,
            notes TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agency_decisions_player ON agency_decisions(player_uid, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_agency_proactive_created ON agency_proactive_decisions(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_agency_feedback_decision ON agency_feedback(agency_decision_id, created_at DESC);
        """
        with self._lock:
            self.conn.executescript(schema)
            self.conn.commit()

    def add_self_reflection(self, content: str, *, provenance: Mapping[str, Any], review_status: str = "PENDING") -> str:
        text = _text(content, "content")
        now = time.time()
        memory_id = uuid.uuid4().hex
        with self._lock:
            self.conn.execute(
                "INSERT INTO peva_self_memory VALUES (?, 'SELF_REFLECTION', ?, ?, ?, 1, ?, ?)",
                (memory_id, text, _json(provenance), _review_status(review_status), now, now),
            )
            self.conn.commit()
        return memory_id

    def list_self_reflections(self, *, active_only: bool = True, limit: int = 8) -> list[dict[str, Any]]:
        clause = "WHERE memory_kind = 'SELF_REFLECTION'" + (" AND active = 1" if active_only else "")
        with self._lock:
            rows = self.conn.execute(f"SELECT * FROM peva_self_memory {clause} ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]

    def record_reactive(self, *, goal_id: str | None, player_uid: str, room_id: str, incoming_text: str, decision: Mapping[str, Any], status: str, provenance: Mapping[str, Any]) -> str:
        decision_id = uuid.uuid4().hex
        with self._lock:
            self.conn.execute(
                "INSERT INTO agency_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision_id, goal_id, player_uid, room_id, incoming_text, str(decision.get("decision") or ""), str(decision.get("reason_code") or ""), decision.get("message"), _text(status, "status"), _json(provenance), time.time()),
            )
            self.conn.commit()
        return decision_id

    def record_proactive(self, *, goal_id: str | None, target_uid: str | None, room_id: str | None, decision: Mapping[str, Any], candidate_uids: Iterable[str], status: str, provenance: Mapping[str, Any]) -> str:
        decision_id = uuid.uuid4().hex
        with self._lock:
            self.conn.execute(
                "INSERT INTO agency_proactive_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision_id, goal_id, target_uid, room_id, str(decision.get("decision") or ""), str(decision.get("reason_code") or ""), decision.get("message"), _json(list(candidate_uids)), _text(status, "status"), _json(provenance), time.time()),
            )
            self.conn.commit()
        return decision_id

    def add_feedback(self, agency_decision_id: str, *, rating: str, corrected_decision: str | None = None, corrected_message: str | None = None, notes: str | None = None) -> str:
        feedback_id = uuid.uuid4().hex
        with self._lock:
            self.conn.execute(
                "INSERT INTO agency_feedback VALUES (?, ?, ?, ?, ?, ?, ?)",
                (feedback_id, _text(agency_decision_id, "agency_decision_id"), _review_status(rating), corrected_decision, corrected_message, notes, time.time()),
            )
            self.conn.commit()
        return feedback_id

    def recent_decisions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM agency_decisions ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM agency_decisions WHERE decision_id = ?", (_text(decision_id, "decision_id"),)).fetchone()
        return dict(row) if row is not None else None


def _text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} must be non-empty")
    return text


def _review_status(value: str) -> str:
    result = _text(value, "rating").upper()
    if result not in {"GOOD", "BAD", "EDITED", "SKIP", "PENDING"}:
        raise ValueError("rating must be GOOD, BAD, EDITED, SKIP, or PENDING")
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
