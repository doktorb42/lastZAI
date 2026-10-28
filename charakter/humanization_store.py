"""Persistent, reviewable humanization data for PEva.

This store is intentionally separate from ``conversation.db``.  It adds
player-specific social data without rewriting historical chat or the existing
memory-review database.  Nothing here can send a message or relax delivery
verification.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from config.runtime_config import DATA_DIR


MEMORY_TYPES = frozenset({
    "IDENTITY", "PREFERENCE", "GAME_PREFERENCE", "SCHEDULE", "SOCIAL",
    "RELATIONSHIP", "RECURRING_TOPIC", "OTHER",
})
RELATIONSHIPS = frozenset({"UNKNOWN", "KNOWN_PLAYER", "ALLY", "FRIEND", "OFFICER", "LEADER"})
EMOJI_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
REPLY_LENGTHS = frozenset({"SHORT", "NORMAL", "DETAILED"})
BAD_REPLY_REASONS = frozenset({
    "TOO_FORMAL", "SOUNDS_LIKE_ASSISTANT", "REPETITIVE", "IGNORED_CONTEXT",
    "IGNORED_MEMORY", "WRONG_TONE", "TOO_LONG", "TOO_SHORT",
    "UNNATURAL_WORDING", "WRONG_EMOTION", "FACTUAL_ERROR", "ACTION_SAFETY", "HALLUCINATION", "OTHER",
})


@dataclass(frozen=True, slots=True)
class PlayerHumanProfile:
    player_uid: str
    relationship: str = "UNKNOWN"
    familiarity: float = 0.0
    trust: float = 0.5
    conversation_summary: str | None = None


@dataclass(frozen=True, slots=True)
class PlayerStyleProfile:
    player_uid: str
    preferred_tone: str | None = None
    softness: float = 0.5
    playfulness: float = 0.5
    teasing: float = 0.0
    directness: float = 0.5
    formality: float = 0.35
    emoji_level: str = "MEDIUM"
    reply_length: str = "SHORT"
    tags: tuple[str, ...] = ()
    origin: str = "automatic"


@dataclass(frozen=True, slots=True)
class HumanMemory:
    memory_id: str
    player_uid: str
    memory_type: str
    memory_key: str | None
    content: str
    source_message_id: int | None
    source_message_key: str | None
    source_text: str | None
    confidence: float
    importance: float
    created_at: float
    updated_at: float
    expires_at: float | None
    active: bool
    origin: str


class HumanizationStore:
    """Idempotent SQLite persistence for H1-H6 and social initiative state."""

    def __init__(self, path: str | Path = DATA_DIR / "databases" / "humanization.db", *,
                 training_root: str | Path = DATA_DIR / "training" / "reviewed") -> None:
        self.path = Path(path)
        self.training_root = Path(training_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA foreign_keys=ON;")
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> "HumanizationStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS player_human_profiles (
            player_uid TEXT PRIMARY KEY,
            relationship TEXT NOT NULL DEFAULT 'UNKNOWN',
            familiarity REAL NOT NULL DEFAULT 0.0,
            trust REAL NOT NULL DEFAULT 0.5,
            conversation_summary TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS player_style_profiles (
            player_uid TEXT PRIMARY KEY,
            preferred_tone TEXT,
            softness REAL NOT NULL DEFAULT 0.5,
            playfulness REAL NOT NULL DEFAULT 0.5,
            teasing REAL NOT NULL DEFAULT 0.0,
            directness REAL NOT NULL DEFAULT 0.5,
            formality REAL NOT NULL DEFAULT 0.35,
            emoji_level TEXT NOT NULL DEFAULT 'MEDIUM',
            reply_length TEXT NOT NULL DEFAULT 'SHORT',
            tags_json TEXT NOT NULL DEFAULT '[]',
            origin TEXT NOT NULL DEFAULT 'automatic',
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS player_social_state (
            player_uid TEXT PRIMARY KEY,
            last_room_id TEXT,
            last_incoming_at REAL,
            last_outgoing_at REAL,
            last_proactive_at REAL,
            proactive_count INTEGER NOT NULL DEFAULT 0,
            ignored_proactive_count INTEGER NOT NULL DEFAULT 0,
            last_proactive_topic TEXT,
            conversation_interest_score REAL NOT NULL DEFAULT 0.0,
            proactive_suppressed_until REAL,
            proactive_suppressed_indefinitely INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS durable_memories (
            memory_id TEXT PRIMARY KEY,
            player_uid TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            memory_key TEXT,
            content TEXT NOT NULL,
            source_message_id INTEGER,
            source_message_key TEXT,
            source_text TEXT,
            confidence REAL NOT NULL,
            importance REAL NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL,
            active INTEGER NOT NULL DEFAULT 1,
            origin TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_human_memory_active ON durable_memories(player_uid, active, importance, updated_at);
        CREATE TABLE IF NOT EXISTS memory_edit_history (
            history_id TEXT PRIMARY KEY,
            memory_id TEXT NOT NULL,
            previous_content TEXT,
            previous_active INTEGER,
            changed_at REAL NOT NULL,
            reason TEXT,
            origin TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_review_candidates (
            candidate_id TEXT PRIMARY KEY,
            player_uid TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            memory_key TEXT,
            content TEXT NOT NULL,
            source_message_id INTEGER,
            source_message_key TEXT,
            source_text TEXT,
            confidence REAL NOT NULL,
            importance REAL NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            origin TEXT NOT NULL DEFAULT 'automatic'
        );
        CREATE TABLE IF NOT EXISTS style_review_candidates (
            candidate_id TEXT PRIMARY KEY,
            player_uid TEXT NOT NULL,
            change_json TEXT NOT NULL,
            source_text TEXT,
            confidence REAL NOT NULL,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
        );
        CREATE TABLE IF NOT EXISTS reply_humanization_reviews (
            review_id TEXT PRIMARY KEY,
            player_uid TEXT NOT NULL,
            room_id TEXT,
            incoming_text TEXT,
            reply_text TEXT NOT NULL,
            original_reply TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            bad_reason TEXT,
            edited_reply TEXT,
            context_json TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reply_history (
            reply_id TEXT PRIMARY KEY,
            player_uid TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_reply_history_player ON reply_history(player_uid, created_at DESC);
        CREATE TABLE IF NOT EXISTS proactive_history (
            proactive_id TEXT PRIMARY KEY,
            player_uid TEXT NOT NULL,
            room_id TEXT,
            initiative_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            topic TEXT,
            score REAL NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            confirmed_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_history_player ON proactive_history(player_uid, created_at DESC);
        CREATE TABLE IF NOT EXISTS proactive_reviews (
            review_id TEXT PRIMARY KEY,
            player_uid TEXT NOT NULL,
            suggestion TEXT,
            approved INTEGER NOT NULL,
            created_at REAL NOT NULL
        );
        """
        with self._lock:
            self.conn.executescript(schema)
            self.conn.commit()

    # Profiles and conservative deterministic signals -----------------

    def observe_incoming(self, *, player_uid: str, room_id: str, text: str, at: float | None = None,
                         relationship: str | None = None) -> PlayerHumanProfile:
        now = time.time() if at is None else float(at)
        uid = _required(player_uid, "player_uid")
        meaningful = _is_meaningful(text)
        with self._lock:
            self._ensure_player_locked(uid, now, relationship=relationship)
            if meaningful:
                self.conn.execute(
                    "UPDATE player_human_profiles SET familiarity = MIN(0.95, familiarity + 0.01), updated_at = ? WHERE player_uid = ?",
                    (now, uid),
                )
            self.conn.execute(
                """INSERT INTO player_social_state (player_uid, last_room_id, last_incoming_at, conversation_interest_score, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(player_uid) DO UPDATE SET last_room_id=excluded.last_room_id,
                     last_incoming_at=excluded.last_incoming_at,
                     conversation_interest_score=MIN(1.0, player_social_state.conversation_interest_score + ?),
                     updated_at=excluded.updated_at""",
                (uid, _optional(room_id), now, 0.04 if meaningful else 0.0, now, 0.04 if meaningful else 0.0),
            )
            self.conn.commit()
        return self.get_profile(uid)

    def get_profile(self, player_uid: str, *, fallback_relationship: str | None = None) -> PlayerHumanProfile:
        uid = _required(player_uid, "player_uid")
        now = time.time()
        with self._lock:
            self._ensure_player_locked(uid, now, relationship=fallback_relationship)
            row = self.conn.execute("SELECT * FROM player_human_profiles WHERE player_uid = ?", (uid,)).fetchone()
            self.conn.commit()
        assert row is not None
        return PlayerHumanProfile(uid, str(row["relationship"]), float(row["familiarity"]), float(row["trust"]), row["conversation_summary"])

    def set_relationship(self, player_uid: str, relationship: str) -> PlayerHumanProfile:
        value = _relationship(relationship)
        uid = _required(player_uid, "player_uid")
        now = time.time()
        with self._lock:
            self._ensure_player_locked(uid, now)
            self.conn.execute("UPDATE player_human_profiles SET relationship = ?, updated_at = ? WHERE player_uid = ?", (value, now, uid))
            self.conn.commit()
        return self.get_profile(uid)

    def set_familiarity(self, player_uid: str, value: float) -> PlayerHumanProfile:
        uid = _required(player_uid, "player_uid")
        now = time.time()
        with self._lock:
            self._ensure_player_locked(uid, now)
            self.conn.execute("UPDATE player_human_profiles SET familiarity = ?, updated_at = ? WHERE player_uid = ?", (_score(value), now, uid))
            self.conn.commit()
        return self.get_profile(uid)

    def set_conversation_summary(self, player_uid: str, summary: str | None) -> None:
        uid = _required(player_uid, "player_uid")
        now = time.time()
        with self._lock:
            self._ensure_player_locked(uid, now)
            self.conn.execute("UPDATE player_human_profiles SET conversation_summary = ?, updated_at = ? WHERE player_uid = ?", (_optional(summary), now, uid))
            self.conn.commit()

    def get_social_state(self, player_uid: str) -> dict[str, Any]:
        uid = _required(player_uid, "player_uid")
        with self._lock:
            row = self.conn.execute("SELECT * FROM player_social_state WHERE player_uid = ?", (uid,)).fetchone()
        return dict(row) if row is not None else {"player_uid": uid}

    def set_proactive_suppression(self, player_uid: str, *, until: float | None = None, indefinite: bool = False) -> None:
        uid = _required(player_uid, "player_uid")
        now = time.time()
        with self._lock:
            self._ensure_player_locked(uid, now)
            self.conn.execute(
                """INSERT INTO player_social_state (player_uid, proactive_suppressed_until, proactive_suppressed_indefinitely, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(player_uid) DO UPDATE SET proactive_suppressed_until=excluded.proactive_suppressed_until,
                     proactive_suppressed_indefinitely=excluded.proactive_suppressed_indefinitely, updated_at=excluded.updated_at""",
                (uid, until, 1 if indefinite else 0, now),
            )
            self.conn.commit()

    # Style ------------------------------------------------------------

    def get_style(self, player_uid: str) -> PlayerStyleProfile:
        uid = _required(player_uid, "player_uid")
        now = time.time()
        with self._lock:
            self._ensure_player_locked(uid, now)
            self.conn.execute("INSERT OR IGNORE INTO player_style_profiles (player_uid, updated_at) VALUES (?, ?)", (uid, now))
            row = self.conn.execute("SELECT * FROM player_style_profiles WHERE player_uid = ?", (uid,)).fetchone()
            self.conn.commit()
        assert row is not None
        return _style_from_row(row)

    def update_style(self, player_uid: str, *, origin: str = "manual", **changes: Any) -> PlayerStyleProfile:
        uid = _required(player_uid, "player_uid")
        allowed = {"preferred_tone", "softness", "playfulness", "teasing", "directness", "formality", "emoji_level", "reply_length", "tags"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown style field(s): {', '.join(sorted(unknown))}")
        values: dict[str, Any] = {}
        for key, value in changes.items():
            if key in {"softness", "playfulness", "teasing", "directness", "formality"}:
                values[key] = _score(value)
            elif key == "emoji_level":
                values[key] = _choice(value, EMOJI_LEVELS, key)
            elif key == "reply_length":
                values[key] = _choice(value, REPLY_LENGTHS, key)
            elif key == "tags":
                values["tags_json"] = json.dumps(_tags(value), ensure_ascii=False)
            else:
                values[key] = _optional(value)
        now = time.time()
        with self._lock:
            self._ensure_player_locked(uid, now)
            self.conn.execute("INSERT OR IGNORE INTO player_style_profiles (player_uid, updated_at) VALUES (?, ?)", (uid, now))
            fields = [f"{key} = ?" for key in values] + ["origin = ?", "updated_at = ?"]
            self.conn.execute(f"UPDATE player_style_profiles SET {', '.join(fields)} WHERE player_uid = ?", (*values.values(), _origin(origin), now, uid))
            self.conn.commit()
        return self.get_style(uid)

    def create_style_candidate(self, *, player_uid: str, changes: dict[str, Any], source_text: str,
                               confidence: float = 0.5) -> str:
        """Queue an explicit preference for review; never apply it blindly."""
        candidate_id = uuid.uuid4().hex
        now = time.time()
        # Validate field names and values using the same path as real edits,
        # but preserve the reviewed proposal instead of changing the profile.
        allowed = {"preferred_tone", "softness", "playfulness", "teasing", "directness", "formality", "emoji_level", "reply_length", "tags"}
        if set(changes) - allowed:
            raise ValueError("Unsupported style candidate field.")
        with self._lock:
            self._ensure_player_locked(_required(player_uid, "player_uid"), now)
            self.conn.execute("INSERT INTO style_review_candidates VALUES (?, ?, ?, ?, ?, ?, 'PENDING')",
                              (candidate_id, player_uid, json.dumps(changes, ensure_ascii=False), _optional(source_text), _score(confidence), now))
            self.conn.commit()
        return candidate_id

    def list_style_candidates(self, *, player_uid: str | None = None, status: str = "PENDING") -> list[dict[str, Any]]:
        query, params = "SELECT * FROM style_review_candidates WHERE status = ?", [str(status).upper()]
        if player_uid:
            query += " AND player_uid = ?"
            params.append(str(player_uid))
        query += " ORDER BY created_at ASC"
        with self._lock:
            return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def approve_style_candidate(self, candidate_id: str) -> PlayerStyleProfile:
        with self._lock:
            row = self.conn.execute("SELECT * FROM style_review_candidates WHERE candidate_id = ?", (str(candidate_id),)).fetchone()
            if row is None:
                raise KeyError(f"Unknown style candidate: {candidate_id}")
            if str(row["status"]) != "PENDING":
                raise ValueError(f"Style candidate is {row['status']}, not reviewable.")
            changes = json.loads(str(row["change_json"]))
            uid = str(row["player_uid"])
        style = self.update_style(uid, origin="reviewed", **changes)
        with self._lock:
            self.conn.execute("UPDATE style_review_candidates SET status='APPROVED' WHERE candidate_id=?", (str(candidate_id),))
            self.conn.commit()
        self._append_reviewed("style_candidates.jsonl", {
            "player_uid": uid, "style": changes, "origin": "reviewed",
        })
        return style

    # Durable memory and review --------------------------------------

    def create_memory_candidate(self, *, player_uid: str, memory_type: str, content: str, memory_key: str | None = None,
                                source_message_id: int | None = None, source_message_key: str | None = None,
                                source_text: str | None = None, confidence: float = 0.5, importance: float = 0.5,
                                expires_at: float | None = None, origin: str = "automatic") -> str:
        candidate_id = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._ensure_player_locked(_required(player_uid, "player_uid"), now)
            self.conn.execute(
                "INSERT INTO memory_review_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (candidate_id, player_uid, _memory_type(memory_type), _optional(memory_key), _required(content, "content"),
                 source_message_id, _optional(source_message_key), _optional(source_text), _score(confidence), _score(importance),
                 now, expires_at, "PENDING", _origin(origin)),
            )
            self.conn.commit()
        return candidate_id

    def list_memory_candidates(self, *, player_uid: str | None = None, status: str = "PENDING") -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_review_candidates WHERE status = ?"
        params: list[Any] = [str(status).upper()]
        if player_uid:
            query += " AND player_uid = ?"
            params.append(str(player_uid))
        query += " ORDER BY created_at ASC"
        with self._lock:
            return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def approve_memory_candidate(self, candidate_id: str, *, origin: str = "reviewed") -> str:
        with self._lock:
            row = self.conn.execute("SELECT * FROM memory_review_candidates WHERE candidate_id = ?", (str(candidate_id),)).fetchone()
            if row is None:
                raise KeyError(f"Unknown memory candidate: {candidate_id}")
            if str(row["status"]) not in {"PENDING", "APPROVED"}:
                raise ValueError(f"Candidate is {row['status']}, not reviewable.")
            memory_id = self._insert_memory_locked(dict(row), origin=_origin(origin))
            self.conn.execute("UPDATE memory_review_candidates SET status = 'APPROVED' WHERE candidate_id = ?", (str(candidate_id),))
            self.conn.commit()
        return memory_id

    def reject_memory_candidate(self, candidate_id: str) -> None:
        with self._lock:
            self.conn.execute("UPDATE memory_review_candidates SET status = 'REJECTED' WHERE candidate_id = ?", (str(candidate_id),))
            self.conn.commit()

    def add_memory(self, *, player_uid: str, memory_type: str, content: str, memory_key: str | None = None,
                   source_message_id: int | None = None, source_message_key: str | None = None, source_text: str | None = None,
                   confidence: float = 0.5, importance: float = 0.5, expires_at: float | None = None,
                   origin: str = "manual") -> str:
        row = {
            "player_uid": _required(player_uid, "player_uid"), "memory_type": _memory_type(memory_type), "memory_key": _optional(memory_key),
            "content": _required(content, "content"), "source_message_id": source_message_id, "source_message_key": _optional(source_message_key),
            "source_text": _optional(source_text), "confidence": _score(confidence), "importance": _score(importance), "expires_at": expires_at,
        }
        with self._lock:
            self._ensure_player_locked(row["player_uid"], time.time())
            memory_id = self._insert_memory_locked(row, origin=_origin(origin))
            self.conn.commit()
        return memory_id

    def get_memories(self, player_uid: str, *, include_inactive: bool = False, limit: int = 12) -> list[HumanMemory]:
        query = "SELECT * FROM durable_memories WHERE player_uid = ?"
        params: list[Any] = [_required(player_uid, "player_uid")]
        if not include_inactive:
            query += " AND active = 1 AND (expires_at IS NULL OR expires_at > ?)"
            params.append(time.time())
        query += " ORDER BY importance DESC, confidence DESC, updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self.conn.execute(query, params).fetchall()
        return [_memory_from_row(row) for row in rows]

    def edit_memory(self, memory_id: str, *, content: str | None = None, active: bool | None = None,
                    memory_type: str | None = None, importance: float | None = None, confidence: float | None = None,
                    reason: str | None = None, origin: str = "edited") -> HumanMemory:
        with self._lock:
            row = self.conn.execute("SELECT * FROM durable_memories WHERE memory_id = ?", (str(memory_id),)).fetchone()
            if row is None:
                raise KeyError(f"Unknown memory: {memory_id}")
            now = time.time()
            self.conn.execute("INSERT INTO memory_edit_history VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (uuid.uuid4().hex, str(memory_id), row["content"], row["active"], now, _optional(reason), _origin(origin)))
            changes: dict[str, Any] = {"updated_at": now, "origin": _origin(origin)}
            if content is not None: changes["content"] = _required(content, "content")
            if active is not None: changes["active"] = 1 if active else 0
            if memory_type is not None: changes["memory_type"] = _memory_type(memory_type)
            if importance is not None: changes["importance"] = _score(importance)
            if confidence is not None: changes["confidence"] = _score(confidence)
            fields = ", ".join(f"{key} = ?" for key in changes)
            self.conn.execute(f"UPDATE durable_memories SET {fields} WHERE memory_id = ?", (*changes.values(), str(memory_id)))
            updated = self.conn.execute("SELECT * FROM durable_memories WHERE memory_id = ?", (str(memory_id),)).fetchone()
            self.conn.commit()
        assert updated is not None
        return _memory_from_row(updated)

    def memory_history(self, memory_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self.conn.execute("SELECT * FROM memory_edit_history WHERE memory_id = ? ORDER BY changed_at", (str(memory_id),)).fetchall()]

    # Reply and initiative review -------------------------------------

    def record_reply_review(self, *, player_uid: str, room_id: str, incoming_text: str, reply_text: str,
                            original_reply: str | None, context: Iterable[str] = ()) -> str:
        review_id = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self.conn.execute("INSERT INTO reply_humanization_reviews VALUES (?, ?, ?, ?, ?, ?, 'PENDING', NULL, NULL, ?, ?, ?)",
                              (review_id, _required(player_uid, "player_uid"), _optional(room_id), str(incoming_text or ""),
                               _required(reply_text, "reply_text"), _optional(original_reply), json.dumps(list(context), ensure_ascii=False), now, now))
            self.conn.execute("INSERT INTO reply_history VALUES (?, ?, ?, ?)", (uuid.uuid4().hex, player_uid, reply_text, now))
            self.conn.commit()
        return review_id

    def review_reply(self, review_id: str, *, status: str, bad_reason: str | None = None, edited_reply: str | None = None) -> None:
        state = str(status).upper()
        if state not in {"GOOD", "BAD", "EDITED", "SKIPPED"}:
            raise ValueError("Unsupported reply review status.")
        reason = _choice(bad_reason, BAD_REPLY_REASONS, "bad_reason") if bad_reason else None
        with self._lock:
            row = self.conn.execute("SELECT * FROM reply_humanization_reviews WHERE review_id = ?", (str(review_id),)).fetchone()
            if row is None:
                raise KeyError(f"Unknown reply review: {review_id}")
            self.conn.execute("UPDATE reply_humanization_reviews SET status=?, bad_reason=?, edited_reply=?, updated_at=? WHERE review_id=?",
                              (state, reason, _optional(edited_reply), time.time(), str(review_id)))
            self.conn.commit()
        if state in {"GOOD", "EDITED"}:
            self._append_reviewed("reply_candidates.jsonl", {
                "review_id": str(review_id), "player_uid": row["player_uid"], "input_message": row["incoming_text"],
                "reply": edited_reply or row["reply_text"], "original_reply": row["original_reply"], "status": state,
            })

    def get_reply_review(self, review_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM reply_humanization_reviews WHERE review_id = ?", (str(review_id),)).fetchone()
        return dict(row) if row is not None else None

    def list_reply_reviews(self, *, status: str | None = "PENDING", limit: int = 30) -> list[dict[str, Any]]:
        query = "SELECT * FROM reply_humanization_reviews"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(str(status).upper())
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            return [dict(row) for row in self.conn.execute(query, params).fetchall()]

    def recent_replies(self, player_uid: str, *, limit: int = 8) -> list[str]:
        with self._lock:
            rows = self.conn.execute("SELECT text FROM reply_history WHERE player_uid = ? ORDER BY created_at DESC LIMIT ?", (_required(player_uid, "player_uid"), max(1, int(limit)))).fetchall()
        return [str(row["text"]) for row in reversed(rows)]

    def record_proactive(self, *, player_uid: str, room_id: str | None, initiative_type: str, reason: str,
                         topic: str | None, score: float, text: str, status: str = "CANDIDATE") -> str:
        item_id = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._ensure_player_locked(_required(player_uid, "player_uid"), now)
            self.conn.execute("INSERT INTO proactive_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                              (item_id, player_uid, _optional(room_id), _required(initiative_type, "initiative_type"), _required(reason, "reason"),
                               _optional(topic), _score(score), _required(text, "text"), _required(status, "status"), now))
            self.conn.commit()
        return item_id

    def confirm_proactive(self, proactive_id: str) -> None:
        now = time.time()
        with self._lock:
            row = self.conn.execute("SELECT * FROM proactive_history WHERE proactive_id = ?", (str(proactive_id),)).fetchone()
            if row is None: raise KeyError(f"Unknown proactive item: {proactive_id}")
            uid = str(row["player_uid"])
            self.conn.execute("UPDATE proactive_history SET status='CONFIRMED', confirmed_at=? WHERE proactive_id=?", (now, str(proactive_id)))
            self.conn.execute("""INSERT INTO player_social_state (player_uid, last_proactive_at, proactive_count, last_proactive_topic, updated_at)
                              VALUES (?, ?, 1, ?, ?)
                              ON CONFLICT(player_uid) DO UPDATE SET last_proactive_at=excluded.last_proactive_at,
                                proactive_count=player_social_state.proactive_count + 1, last_proactive_topic=excluded.last_proactive_topic, updated_at=excluded.updated_at""",
                              (uid, now, row["topic"], now))
            self.conn.commit()

    def record_outgoing(self, player_uid: str, *, at: float | None = None) -> None:
        now = time.time() if at is None else float(at)
        with self._lock:
            self._ensure_player_locked(_required(player_uid, "player_uid"), now)
            self.conn.execute("""INSERT INTO player_social_state (player_uid, last_outgoing_at, updated_at) VALUES (?, ?, ?)
                              ON CONFLICT(player_uid) DO UPDATE SET last_outgoing_at=excluded.last_outgoing_at, updated_at=excluded.updated_at""", (player_uid, now, now))
            self.conn.commit()

    def list_social_candidates(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute("""SELECT p.*, s.* FROM player_human_profiles p
                                      LEFT JOIN player_social_state s ON s.player_uid=p.player_uid
                                      ORDER BY s.last_incoming_at DESC""").fetchall()
        return [dict(row) for row in rows]

    def proactive_count_since(self, timestamp: float) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS count FROM proactive_history WHERE created_at >= ? AND status IN ('CANDIDATE', 'QUEUED', 'CONFIRMED')",
                (float(timestamp),),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def add_proactive_review(self, *, player_uid: str, approved: bool, suggestion: str | None = None) -> None:
        with self._lock:
            self.conn.execute("INSERT INTO proactive_reviews VALUES (?, ?, ?, ?, ?)",
                              (uuid.uuid4().hex, _required(player_uid, "player_uid"), _optional(suggestion), 1 if approved else 0, time.time()))
            self.conn.commit()
        self._append_reviewed("proactive_candidates.jsonl", {
            "player_uid": player_uid, "approved": bool(approved), "suggestion": _optional(suggestion),
        })

    def _ensure_player_locked(self, uid: str, now: float, relationship: str | None = None) -> None:
        rel = _relationship(relationship) if relationship else "UNKNOWN"
        self.conn.execute("""INSERT INTO player_human_profiles (player_uid, relationship, created_at, updated_at)
                           VALUES (?, ?, ?, ?) ON CONFLICT(player_uid) DO NOTHING""", (uid, rel, now, now))

    def _insert_memory_locked(self, row: dict[str, Any], *, origin: str) -> str:
        memory_id = uuid.uuid4().hex
        now = time.time()
        self.conn.execute("""INSERT INTO durable_memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                          (memory_id, row["player_uid"], row["memory_type"], row.get("memory_key"), row["content"],
                           row.get("source_message_id"), row.get("source_message_key"), row.get("source_text"), row["confidence"], row["importance"],
                           now, now, row.get("expires_at"), origin))
        return memory_id

    def _append_reviewed(self, filename: str, payload: dict[str, Any]) -> None:
        self.training_root.mkdir(parents=True, exist_ok=True)
        record = {"reviewed_at": time.time(), **payload}
        with (self.training_root / filename).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _memory_from_row(row: sqlite3.Row) -> HumanMemory:
    return HumanMemory(str(row["memory_id"]), str(row["player_uid"]), str(row["memory_type"]), row["memory_key"], str(row["content"]),
                       row["source_message_id"], row["source_message_key"], row["source_text"], float(row["confidence"]), float(row["importance"]),
                       float(row["created_at"]), float(row["updated_at"]), row["expires_at"], bool(row["active"]), str(row["origin"]))


def _style_from_row(row: sqlite3.Row) -> PlayerStyleProfile:
    try: tags = tuple(str(item) for item in json.loads(str(row["tags_json"])) if str(item).strip())
    except (ValueError, TypeError): tags = ()
    return PlayerStyleProfile(str(row["player_uid"]), row["preferred_tone"], float(row["softness"]), float(row["playfulness"]),
                              float(row["teasing"]), float(row["directness"]), float(row["formality"]), str(row["emoji_level"]),
                              str(row["reply_length"]), tags, str(row["origin"]))


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text: raise ValueError(f"{name} cannot be empty.")
    return text


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _score(value: Any) -> float:
    result = float(value)
    if not 0.0 <= result <= 1.0: raise ValueError("score must be between 0.0 and 1.0.")
    return result


def _choice(value: Any, allowed: frozenset[str], name: str) -> str:
    text = _required(value, name).upper()
    if text not in allowed: raise ValueError(f"Unsupported {name}: {value!r}")
    return text


def _memory_type(value: Any) -> str:
    return _choice(value, MEMORY_TYPES, "memory_type")


def _relationship(value: Any) -> str:
    return _choice(value, RELATIONSHIPS, "relationship")


def _origin(value: Any) -> str:
    text = _required(value, "origin").lower()
    if text not in {"automatic", "manual", "reviewed", "edited"}:
        raise ValueError(f"Unsupported origin: {value!r}")
    return text


def _tags(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
    return list(dict.fromkeys(item.strip().lower() for item in values if str(item).strip()))[:12]


def _is_meaningful(text: str) -> bool:
    normalized = " ".join(str(text or "").casefold().split())
    return len(normalized) >= 8 and normalized not in {"lol", "yes", "yeah", "wait 2 min", "coming", "brb", "ok", "okay"}
