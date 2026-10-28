from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from config.runtime_config import CONVERSATION_DB_PATH
from charakter.social_context import PlayerProfile, Relationship

from .lastz_ai import ChatTurn

if TYPE_CHECKING:
    from archive.legacy_runtime.chat_capture import ChatMessage


DEFAULT_DB_PATH = CONVERSATION_DB_PATH


# ======================================================================
# DATA MODELS
# ======================================================================


@dataclass(slots=True)
class StoredPlayer:
    uid: str
    name: str | None = None
    alliance: str | None = None
    server_id: int | None = None
    language: str | None = None
    first_seen_at: int | None = None
    last_seen_at: int | None = None


@dataclass(slots=True)
class StoredMessage:
    id: int
    room_id: str
    player_uid: str
    direction: str
    role: str
    text: str

    seq_id: int | None = None
    source_message_key: str | None = None

    sender_uid: str | None = None
    sender_name: str | None = None
    sender_alliance: str | None = None
    sender_server_id: int | None = None

    server_time: int | None = None
    send_time: int | None = None
    created_at: int | None = None

    message_type: int | None = None
    post_type: int | None = None
    original_lang: str | None = None
    pushseq: str | None = None

    raw_data: dict[str, Any] | None = None


@dataclass(slots=True)
class PlayerMemory:
    id: int
    player_uid: str
    memory_type: str
    content: str

    memory_key: str | None = None
    importance: float = 0.5
    confidence: float = 0.5

    active: bool = True
    source_message_id: int | None = None

    created_at: int | None = None
    updated_at: int | None = None
    last_used_at: int | None = None
    expires_at: int | None = None


@dataclass(slots=True)
class ConversationSummary:
    id: int
    room_id: str
    player_uid: str
    summary: str

    start_message_id: int | None = None
    end_message_id: int | None = None
    message_count: int = 0

    created_at: int | None = None
    updated_at: int | None = None


@dataclass(slots=True)
class AIConversationContext:
    """
    Ready-to-use context bundle for lastz_ai.py / agent.py.
    """
    history: list[ChatTurn]
    memories: list[str]
    summary: str | None
    player: StoredPlayer | None


# ======================================================================
# STORE
# ======================================================================


class ConversationStore:
    """
    Persistent SQLite memory for PEva.

    Layer 1: full chat history
        messages

    Layer 2: long-term player facts
        player_memory

    Layer 3: rolling summaries of older conversation
        conversation_summaries

    This module does NOT decide what is worth remembering.
    Later the AI/agent memory extractor will call add_memory()/upsert_memory()
    only for useful facts.
    """

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()

        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")

        self._create_schema()

    # ------------------------------------------------------------------
    # LIFECYCLE
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "ConversationStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # SCHEMA
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS players (
            uid TEXT PRIMARY KEY,
            name TEXT,
            alliance TEXT,
            server_id INTEGER,
            language TEXT,
            first_seen_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            room_id TEXT PRIMARY KEY,
            player_uid TEXT NOT NULL,
            group_name TEXT NOT NULL DEFAULT 'custom',
            first_seen_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL,
            FOREIGN KEY (player_uid)
                REFERENCES players(uid)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        -- Stage 2 extension: separate, additive social metadata. Existing
        -- players/messages tables are intentionally left unchanged.
        CREATE TABLE IF NOT EXISTS player_social_profiles (
            uid TEXT PRIMARY KEY,
            relationship TEXT NOT NULL DEFAULT 'UNKNOWN',
            trust_level REAL NOT NULL DEFAULT 0.5,
            messages_received INTEGER NOT NULL DEFAULT 0,
            messages_sent INTEGER NOT NULL DEFAULT 0,
            last_private_message_at INTEGER,
            last_alliance_message_at INTEGER,
            last_reply_at INTEGER,
            notes TEXT,
            FOREIGN KEY (uid)
                REFERENCES players(uid)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            room_id TEXT NOT NULL,
            player_uid TEXT NOT NULL,

            direction TEXT NOT NULL
                CHECK(direction IN ('incoming', 'outgoing')),

            role TEXT NOT NULL
                CHECK(role IN ('user', 'assistant')),

            text TEXT NOT NULL,

            seq_id INTEGER,
            source_message_key TEXT UNIQUE,

            sender_uid TEXT,
            sender_name TEXT,
            sender_alliance TEXT,
            sender_server_id INTEGER,

            server_time INTEGER,
            send_time INTEGER,
            created_at INTEGER NOT NULL,

            message_type INTEGER,
            post_type INTEGER,
            original_lang TEXT,
            pushseq TEXT,

            raw_data_json TEXT,

            FOREIGN KEY (room_id)
                REFERENCES conversations(room_id)
                ON UPDATE CASCADE
                ON DELETE CASCADE,

            FOREIGN KEY (player_uid)
                REFERENCES players(uid)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS player_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            player_uid TEXT NOT NULL,

            memory_type TEXT NOT NULL,
            memory_key TEXT,
            content TEXT NOT NULL,

            importance REAL NOT NULL DEFAULT 0.5
                CHECK(importance >= 0.0 AND importance <= 1.0),

            confidence REAL NOT NULL DEFAULT 0.5
                CHECK(confidence >= 0.0 AND confidence <= 1.0),

            active INTEGER NOT NULL DEFAULT 1
                CHECK(active IN (0, 1)),

            source_message_id INTEGER,

            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            last_used_at INTEGER,
            expires_at INTEGER,

            FOREIGN KEY (player_uid)
                REFERENCES players(uid)
                ON UPDATE CASCADE
                ON DELETE CASCADE,

            FOREIGN KEY (source_message_id)
                REFERENCES messages(id)
                ON UPDATE CASCADE
                ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            room_id TEXT NOT NULL,
            player_uid TEXT NOT NULL,

            summary TEXT NOT NULL,

            start_message_id INTEGER,
            end_message_id INTEGER,
            message_count INTEGER NOT NULL DEFAULT 0,

            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,

            FOREIGN KEY (room_id)
                REFERENCES conversations(room_id)
                ON UPDATE CASCADE
                ON DELETE CASCADE,

            FOREIGN KEY (player_uid)
                REFERENCES players(uid)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_room_id_id
            ON messages(room_id, id);

        CREATE INDEX IF NOT EXISTS idx_messages_player_uid_id
            ON messages(player_uid, id);

        CREATE INDEX IF NOT EXISTS idx_messages_server_time
            ON messages(server_time);

        CREATE INDEX IF NOT EXISTS idx_conversations_player_uid
            ON conversations(player_uid);

        CREATE INDEX IF NOT EXISTS idx_social_profiles_relationship
            ON player_social_profiles(relationship);

        CREATE INDEX IF NOT EXISTS idx_player_memory_player_active
            ON player_memory(player_uid, active, importance, updated_at);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_player_memory_key_unique
            ON player_memory(player_uid, memory_key)
            WHERE memory_key IS NOT NULL;

        CREATE INDEX IF NOT EXISTS idx_summaries_room_updated
            ON conversation_summaries(room_id, updated_at);
        """

        with self._lock:
            self._conn.executescript(schema)
            self._conn.commit()

    # ==================================================================
    # INCOMING MESSAGES
    # ==================================================================

    def save_incoming(
        self,
        message: "ChatMessage | Any",
    ) -> bool:
        """
        Store one incoming chat_capture.ChatMessage.

        True  = inserted
        False = duplicate
        """

        room_id = self._required_str(
            getattr(message, "room_id", None),
            "message.room_id",
        )

        sender_uid = self._required_str(
            getattr(message, "sender_uid", None),
            "message.sender_uid",
        )

        text = str(
            getattr(message, "text", "") or ""
        )

        seq_id = self._optional_int(
            getattr(message, "seq_id", None)
        )

        source_message_key = getattr(
            message,
            "source_message_key",
            None,
        )

        if callable(source_message_key):
            source_message_key = source_message_key()

        source_message_key = (
            str(source_message_key)
            if source_message_key
            else None
        )

        server_time = self._optional_int(
            getattr(message, "server_time", None)
        )
        send_time = self._optional_int(
            getattr(message, "send_time", None)
        )

        if not source_message_key:
            if seq_id is not None:
                source_message_key = f"{room_id}:{seq_id}"
            else:
                source_message_key = self._fallback_message_key(
                    room_id=room_id,
                    sender_uid=sender_uid,
                    server_time=server_time,
                    text=text,
                )

        sender_name = self._optional_str(
            getattr(message, "sender_name", None)
        )
        sender_alliance = self._optional_str(
            getattr(message, "sender_alliance", None)
        )
        sender_server_id = self._optional_int(
            getattr(message, "sender_server_id", None)
        )
        original_lang = self._optional_str(
            getattr(message, "original_lang", None)
        )

        message_type = self._optional_int(
            getattr(message, "message_type", None)
        )
        post_type = self._optional_int(
            getattr(message, "post_type", None)
        )
        pushseq = self._optional_str(
            getattr(message, "pushseq", None)
        )

        group_name = (
            self._optional_str(
                getattr(message, "group", None)
            )
            or "custom"
        )

        raw_json = self._json_dumps(
            getattr(message, "raw_data", None)
        )

        now = self._now_ms()
        seen_at = (
            server_time
            or (send_time * 1000 if send_time is not None else now)
        )

        with self._lock:
            self._upsert_player_locked(
                uid=sender_uid,
                name=sender_name,
                alliance=sender_alliance,
                server_id=sender_server_id,
                language=original_lang,
                seen_at=seen_at,
            )

            self._upsert_conversation_locked(
                room_id=room_id,
                player_uid=sender_uid,
                group_name=group_name,
                seen_at=seen_at,
            )

            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    room_id,
                    player_uid,
                    direction,
                    role,
                    text,
                    seq_id,
                    source_message_key,
                    sender_uid,
                    sender_name,
                    sender_alliance,
                    sender_server_id,
                    server_time,
                    send_time,
                    created_at,
                    message_type,
                    post_type,
                    original_lang,
                    pushseq,
                    raw_data_json
                )
                VALUES (
                    ?, ?, 'incoming', 'user', ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?
                );
                """,
                (
                    room_id,
                    sender_uid,
                    text,
                    seq_id,
                    source_message_key,
                    sender_uid,
                    sender_name,
                    sender_alliance,
                    sender_server_id,
                    server_time,
                    send_time,
                    now,
                    message_type,
                    post_type,
                    original_lang,
                    pushseq,
                    raw_json,
                ),
            )

            inserted = cursor.rowcount > 0
            self._conn.commit()

        return inserted

    # ==================================================================
    # OUTGOING / AI REPLIES
    # ==================================================================

    def save_outgoing(
        self,
        *,
        room_id: str,
        player_uid: str,
        text: str,
        sender_uid: str | None = None,
        sender_name: str | None = "PEva",
        source_message_key: str | None = None,
        server_time: int | None = None,
        send_time: int | None = None,
        raw_data: dict[str, Any] | None = None,
    ) -> int:
        """
        Save only after the reply was actually confirmed as sent.
        """

        room_id = self._required_str(room_id, "room_id")
        player_uid = self._required_str(player_uid, "player_uid")
        text = str(text or "").strip()

        if not text:
            raise ValueError("Outgoing text cannot be empty.")

        now = self._now_ms()

        with self._lock:
            self._upsert_player_locked(
                uid=player_uid,
                name=None,
                alliance=None,
                server_id=None,
                language=None,
                seen_at=now,
            )

            self._upsert_conversation_locked(
                room_id=room_id,
                player_uid=player_uid,
                group_name="custom",
                seen_at=now,
            )

            cursor = self._conn.execute(
                """
                INSERT INTO messages (
                    room_id,
                    player_uid,
                    direction,
                    role,
                    text,
                    source_message_key,
                    sender_uid,
                    sender_name,
                    server_time,
                    send_time,
                    created_at,
                    raw_data_json
                )
                VALUES (
                    ?, ?, 'outgoing', 'assistant', ?,
                    ?, ?, ?,
                    ?, ?, ?, ?
                );
                """,
                (
                    room_id,
                    player_uid,
                    text,
                    source_message_key,
                    sender_uid,
                    sender_name,
                    self._optional_int(server_time),
                    self._optional_int(send_time),
                    now,
                    self._json_dumps(raw_data),
                ),
            )

            message_id = int(cursor.lastrowid)
            self._conn.commit()

        return message_id

    # ==================================================================
    # RECENT HISTORY FOR AI
    # ==================================================================

    def get_history(
        self,
        *,
        player_uid: str | None = None,
        room_id: str | None = None,
        limit: int = 20,
    ) -> list[ChatTurn]:
        if not room_id and not player_uid:
            raise ValueError("Provide room_id or player_uid.")

        limit = max(0, int(limit))
        if limit == 0:
            return []

        if room_id:
            where = "room_id = ?"
            value = str(room_id)
        else:
            where = "player_uid = ?"
            value = str(player_uid)

        query = f"""
            SELECT role, text
            FROM (
                SELECT id, role, text
                FROM messages
                WHERE {where}
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC;
        """

        with self._lock:
            rows = self._conn.execute(
                query,
                (value, limit),
            ).fetchall()

        return [
            ChatTurn(
                role=str(row["role"]),
                content=str(row["text"]),
            )
            for row in rows
        ]

    def get_history_dicts(
        self,
        *,
        player_uid: str | None = None,
        room_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        return [
            {
                "role": turn.role,
                "content": turn.content,
            }
            for turn in self.get_history(
                player_uid=player_uid,
                room_id=room_id,
                limit=limit,
            )
        ]

    # ==================================================================
    # LONG-TERM PLAYER MEMORY
    # ==================================================================

    def add_memory(
        self,
        *,
        player_uid: str,
        memory_type: str,
        content: str,
        importance: float = 0.5,
        confidence: float = 0.5,
        source_message_id: int | None = None,
        expires_at: int | None = None,
        memory_key: str | None = None,
    ) -> int:
        """
        Add one long-term fact.

        Use memory_key for facts that should be replaceable later, e.g.:
            alliance.current
            work.schedule
            event.preference.tyrant

        If memory_key already exists, the existing memory is updated.
        """

        player_uid = self._required_str(
            player_uid,
            "player_uid",
        )
        memory_type = self._required_str(
            memory_type,
            "memory_type",
        )
        content = self._required_str(
            content,
            "content",
        )

        importance = self._score(
            importance,
            "importance",
        )
        confidence = self._score(
            confidence,
            "confidence",
        )

        memory_key = self._optional_str(memory_key)
        now = self._now_ms()

        with self._lock:
            self._upsert_player_locked(
                uid=player_uid,
                name=None,
                alliance=None,
                server_id=None,
                language=None,
                seen_at=now,
            )

            if memory_key:
                row = self._conn.execute(
                    """
                    SELECT id
                    FROM player_memory
                    WHERE player_uid = ?
                      AND memory_key = ?
                    LIMIT 1;
                    """,
                    (player_uid, memory_key),
                ).fetchone()

                if row is not None:
                    memory_id = int(row["id"])

                    self._conn.execute(
                        """
                        UPDATE player_memory
                        SET
                            memory_type = ?,
                            content = ?,
                            importance = ?,
                            confidence = ?,
                            active = 1,
                            source_message_id = ?,
                            updated_at = ?,
                            expires_at = ?
                        WHERE id = ?;
                        """,
                        (
                            memory_type,
                            content,
                            importance,
                            confidence,
                            self._optional_int(source_message_id),
                            now,
                            self._optional_int(expires_at),
                            memory_id,
                        ),
                    )
                    self._conn.commit()
                    return memory_id

            cursor = self._conn.execute(
                """
                INSERT INTO player_memory (
                    player_uid,
                    memory_type,
                    memory_key,
                    content,
                    importance,
                    confidence,
                    active,
                    source_message_id,
                    created_at,
                    updated_at,
                    last_used_at,
                    expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, ?);
                """,
                (
                    player_uid,
                    memory_type,
                    memory_key,
                    content,
                    importance,
                    confidence,
                    self._optional_int(source_message_id),
                    now,
                    now,
                    self._optional_int(expires_at),
                ),
            )

            memory_id = int(cursor.lastrowid)
            self._conn.commit()

        return memory_id

    def update_memory(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        memory_type: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        active: bool | None = None,
        expires_at: int | None = None,
    ) -> bool:
        fields: list[str] = []
        values: list[Any] = []

        if content is not None:
            fields.append("content = ?")
            values.append(self._required_str(content, "content"))

        if memory_type is not None:
            fields.append("memory_type = ?")
            values.append(
                self._required_str(memory_type, "memory_type")
            )

        if importance is not None:
            fields.append("importance = ?")
            values.append(self._score(importance, "importance"))

        if confidence is not None:
            fields.append("confidence = ?")
            values.append(self._score(confidence, "confidence"))

        if active is not None:
            fields.append("active = ?")
            values.append(1 if active else 0)

        if expires_at is not None:
            fields.append("expires_at = ?")
            values.append(self._optional_int(expires_at))

        if not fields:
            return False

        fields.append("updated_at = ?")
        values.append(self._now_ms())

        values.append(int(memory_id))

        with self._lock:
            cursor = self._conn.execute(
                f"""
                UPDATE player_memory
                SET {", ".join(fields)}
                WHERE id = ?;
                """,
                values,
            )
            self._conn.commit()

        return cursor.rowcount > 0

    def deactivate_memory(
        self,
        memory_id: int,
    ) -> bool:
        return self.update_memory(
            memory_id,
            active=False,
        )

    def get_memories(
        self,
        *,
        player_uid: str,
        limit: int = 12,
        min_importance: float = 0.0,
        memory_types: Sequence[str] | None = None,
        include_inactive: bool = False,
        mark_used: bool = False,
    ) -> list[PlayerMemory]:
        """
        Returns best memories first:
            importance DESC,
            confidence DESC,
            updated_at DESC
        """

        player_uid = self._required_str(
            player_uid,
            "player_uid",
        )

        limit = max(1, int(limit))
        min_importance = self._score(
            min_importance,
            "min_importance",
        )

        clauses = [
            "player_uid = ?",
            "importance >= ?",
            "(expires_at IS NULL OR expires_at > ?)",
        ]
        params: list[Any] = [
            player_uid,
            min_importance,
            self._now_ms(),
        ]

        if not include_inactive:
            clauses.append("active = 1")

        if memory_types:
            cleaned_types = [
                self._required_str(value, "memory_type")
                for value in memory_types
            ]

            placeholders = ",".join(
                "?"
                for _ in cleaned_types
            )

            clauses.append(
                f"memory_type IN ({placeholders})"
            )
            params.extend(cleaned_types)

        params.append(limit)

        query = f"""
            SELECT *
            FROM player_memory
            WHERE {" AND ".join(clauses)}
            ORDER BY
                importance DESC,
                confidence DESC,
                updated_at DESC
            LIMIT ?;
        """

        with self._lock:
            rows = self._conn.execute(
                query,
                params,
            ).fetchall()

            memories = [
                self._row_to_memory(row)
                for row in rows
            ]

            if mark_used and memories:
                now = self._now_ms()
                ids = [memory.id for memory in memories]
                placeholders = ",".join("?" for _ in ids)

                self._conn.execute(
                    f"""
                    UPDATE player_memory
                    SET last_used_at = ?
                    WHERE id IN ({placeholders});
                    """,
                    [now, *ids],
                )
                self._conn.commit()

        return memories

    def get_memory_context(
        self,
        *,
        player_uid: str,
        limit: int = 12,
        min_importance: float = 0.25,
        mark_used: bool = True,
    ) -> list[str]:
        """
        Simple strings ready for lastz_ai.extra_context.
        """

        memories = self.get_memories(
            player_uid=player_uid,
            limit=limit,
            min_importance=min_importance,
            mark_used=mark_used,
        )

        return [
            f"{memory.memory_type}: {memory.content}"
            for memory in memories
        ]

    # ==================================================================
    # CONVERSATION SUMMARIES
    # ==================================================================

    def save_summary(
        self,
        *,
        room_id: str,
        player_uid: str,
        summary: str,
        start_message_id: int | None = None,
        end_message_id: int | None = None,
        message_count: int = 0,
        replace_latest: bool = True,
    ) -> int:
        """
        Save/update the rolling summary for older conversation.

        Full messages remain in messages table; summary is only an efficient
        context representation for the model.
        """

        room_id = self._required_str(room_id, "room_id")
        player_uid = self._required_str(
            player_uid,
            "player_uid",
        )
        summary = self._required_str(summary, "summary")

        now = self._now_ms()

        with self._lock:
            self._upsert_player_locked(
                uid=player_uid,
                name=None,
                alliance=None,
                server_id=None,
                language=None,
                seen_at=now,
            )

            self._upsert_conversation_locked(
                room_id=room_id,
                player_uid=player_uid,
                group_name="custom",
                seen_at=now,
            )

            if replace_latest:
                row = self._conn.execute(
                    """
                    SELECT id
                    FROM conversation_summaries
                    WHERE room_id = ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1;
                    """,
                    (room_id,),
                ).fetchone()

                if row is not None:
                    summary_id = int(row["id"])

                    self._conn.execute(
                        """
                        UPDATE conversation_summaries
                        SET
                            player_uid = ?,
                            summary = ?,
                            start_message_id = ?,
                            end_message_id = ?,
                            message_count = ?,
                            updated_at = ?
                        WHERE id = ?;
                        """,
                        (
                            player_uid,
                            summary,
                            self._optional_int(start_message_id),
                            self._optional_int(end_message_id),
                            max(0, int(message_count)),
                            now,
                            summary_id,
                        ),
                    )
                    self._conn.commit()
                    return summary_id

            cursor = self._conn.execute(
                """
                INSERT INTO conversation_summaries (
                    room_id,
                    player_uid,
                    summary,
                    start_message_id,
                    end_message_id,
                    message_count,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    room_id,
                    player_uid,
                    summary,
                    self._optional_int(start_message_id),
                    self._optional_int(end_message_id),
                    max(0, int(message_count)),
                    now,
                    now,
                ),
            )

            summary_id = int(cursor.lastrowid)
            self._conn.commit()

        return summary_id

    def get_latest_summary(
        self,
        *,
        room_id: str,
    ) -> ConversationSummary | None:
        room_id = self._required_str(room_id, "room_id")

        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM conversation_summaries
                WHERE room_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1;
                """,
                (room_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_summary(row)

    # ==================================================================
    # COMBINED CONTEXT FOR AI
    # ==================================================================

    def get_ai_context(
        self,
        *,
        player_uid: str,
        room_id: str,
        recent_history_limit: int = 20,
        memory_limit: int = 12,
        min_memory_importance: float = 0.25,
    ) -> AIConversationContext:
        """
        One method the future agent.py can call before generating a reply.
        """

        history = self.get_history(
            room_id=room_id,
            limit=recent_history_limit,
        )

        memories = self.get_memory_context(
            player_uid=player_uid,
            limit=memory_limit,
            min_importance=min_memory_importance,
            mark_used=True,
        )

        summary_obj = self.get_latest_summary(
            room_id=room_id,
        )

        player = self.get_player(
            player_uid
        )

        return AIConversationContext(
            history=history,
            memories=memories,
            summary=(
                summary_obj.summary
                if summary_obj is not None
                else None
            ),
            player=player,
        )

    # ==================================================================
    # STAGE 2 SOCIAL PLAYER PROFILES
    # ==================================================================

    def observe_social_message(
        self,
        *,
        uid: str,
        name: str | None = None,
        alliance: str | None = None,
        server_id: int | None = None,
        group: str,
        seen_at: int | None = None,
    ) -> PlayerProfile:
        """Safely update additive social counters for one captured message."""
        uid = self._required_str(uid, "uid")
        group = str(group or "").strip().lower()
        now = self._now_ms() if seen_at is None else int(seen_at)
        private_at = now if group == "custom" else None
        alliance_at = now if group == "alliance" else None
        with self._lock:
            self._upsert_player_locked(
                uid=uid,
                name=self._optional_str(name),
                alliance=self._optional_str(alliance),
                server_id=self._optional_int(server_id),
                language=None,
                seen_at=now,
            )
            self._conn.execute(
                """
                INSERT INTO player_social_profiles (
                    uid, messages_received, last_private_message_at,
                    last_alliance_message_at
                ) VALUES (?, 1, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    messages_received = player_social_profiles.messages_received + 1,
                    last_private_message_at = COALESCE(
                        excluded.last_private_message_at,
                        player_social_profiles.last_private_message_at
                    ),
                    last_alliance_message_at = COALESCE(
                        excluded.last_alliance_message_at,
                        player_social_profiles.last_alliance_message_at
                    );
                """,
                (uid, private_at, alliance_at),
            )
            self._conn.commit()
        profile = self.get_player_profile(uid)
        if profile is None:
            raise RuntimeError("Social profile was not persisted.")
        return profile

    def get_player_profile(self, uid: str) -> PlayerProfile | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    p.uid, p.name, p.alliance, p.server_id,
                    p.first_seen_at, p.last_seen_at,
                    s.relationship, s.trust_level,
                    s.messages_received, s.messages_sent,
                    s.last_private_message_at, s.last_alliance_message_at,
                    s.last_reply_at, s.notes
                FROM players AS p
                LEFT JOIN player_social_profiles AS s ON s.uid = p.uid
                WHERE p.uid = ?;
                """,
                (str(uid),),
            ).fetchone()
        if row is None:
            return None
        return PlayerProfile(
            uid=str(row["uid"]),
            name=row["name"],
            alliance=row["alliance"],
            server_id=row["server_id"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            relationship=_relationship_or_unknown(row["relationship"]),
            trust_level=float(row["trust_level"] if row["trust_level"] is not None else 0.5),
            messages_received=int(row["messages_received"] or 0),
            messages_sent=int(row["messages_sent"] or 0),
            last_private_message_at=row["last_private_message_at"],
            last_alliance_message_at=row["last_alliance_message_at"],
            last_reply_at=row["last_reply_at"],
            notes=row["notes"],
        )

    def set_player_relationship(
        self,
        uid: str,
        relationship: Relationship | str,
    ) -> PlayerProfile:
        """Manually set a small relationship label without a destructive migration."""
        uid = self._required_str(uid, "uid")
        try:
            value = Relationship(str(getattr(relationship, "value", relationship)).upper())
        except ValueError as exc:
            raise ValueError(f"Unsupported relationship: {relationship!r}") from exc
        now = self._now_ms()
        with self._lock:
            self._upsert_player_locked(
                uid=uid, name=None, alliance=None, server_id=None,
                language=None, seen_at=now,
            )
            self._conn.execute(
                """
                INSERT INTO player_social_profiles (uid, relationship)
                VALUES (?, ?)
                ON CONFLICT(uid) DO UPDATE SET relationship = excluded.relationship;
                """,
                (uid, value.value),
            )
            self._conn.commit()
        profile = self.get_player_profile(uid)
        if profile is None:
            raise RuntimeError("Social profile was not persisted.")
        return profile

    def record_social_reply(
        self,
        *,
        uid: str,
        sent: bool,
        replied_at: int | None = None,
    ) -> None:
        """Track confirmed sends only; preview candidates never inflate sent counts."""
        uid = self._required_str(uid, "uid")
        now = self._now_ms() if replied_at is None else int(replied_at)
        with self._lock:
            self._upsert_player_locked(
                uid=uid, name=None, alliance=None, server_id=None,
                language=None, seen_at=now,
            )
            self._conn.execute(
                """
                INSERT INTO player_social_profiles (uid, messages_sent, last_reply_at)
                VALUES (?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    messages_sent = player_social_profiles.messages_sent + excluded.messages_sent,
                    last_reply_at = excluded.last_reply_at;
                """,
                (uid, 1 if sent else 0, now),
            )
            self._conn.commit()

    # ==================================================================
    # READ API
    # ==================================================================

    def get_player(
        self,
        uid: str,
    ) -> StoredPlayer | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM players
                WHERE uid = ?;
                """,
                (str(uid),),
            ).fetchone()

        if row is None:
            return None

        return StoredPlayer(
            uid=row["uid"],
            name=row["name"],
            alliance=row["alliance"],
            server_id=row["server_id"],
            language=row["language"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
        )

    def get_message_by_source_key(
        self,
        source_message_key: str,
    ) -> StoredMessage | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM messages
                WHERE source_message_key = ?
                LIMIT 1;
                """,
                (str(source_message_key),),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_message(row)

    def get_messages(
        self,
        *,
        room_id: str | None = None,
        player_uid: str | None = None,
        limit: int = 50,
    ) -> list[StoredMessage]:
        if not room_id and not player_uid:
            raise ValueError("Provide room_id or player_uid.")

        limit = max(1, int(limit))

        if room_id:
            where = "room_id = ?"
            value = str(room_id)
        else:
            where = "player_uid = ?"
            value = str(player_uid)

        query = f"""
            SELECT *
            FROM (
                SELECT *
                FROM messages
                WHERE {where}
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC;
        """

        with self._lock:
            rows = self._conn.execute(
                query,
                (value, limit),
            ).fetchall()

        return [
            self._row_to_message(row)
            for row in rows
        ]

    def list_recent_conversations(
        self,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = max(1, int(limit))

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    c.room_id,
                    c.player_uid,
                    c.group_name,
                    c.first_seen_at,
                    c.last_seen_at,
                    p.name,
                    p.alliance,
                    p.server_id,
                    p.language,
                    (
                        SELECT m.text
                        FROM messages AS m
                        WHERE m.room_id = c.room_id
                        ORDER BY m.id DESC
                        LIMIT 1
                    ) AS last_message,
                    (
                        SELECT m.direction
                        FROM messages AS m
                        WHERE m.room_id = c.room_id
                        ORDER BY m.id DESC
                        LIMIT 1
                    ) AS last_direction
                FROM conversations AS c
                LEFT JOIN players AS p
                    ON p.uid = c.player_uid
                ORDER BY c.last_seen_at DESC
                LIMIT ?;
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def has_source_message_key(
        self,
        source_message_key: str,
    ) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1
                FROM messages
                WHERE source_message_key = ?
                LIMIT 1;
                """,
                (str(source_message_key),),
            ).fetchone()

        return row is not None

    def message_count(
        self,
        *,
        room_id: str | None = None,
        player_uid: str | None = None,
    ) -> int:
        where = ""
        params: tuple[Any, ...] = ()

        if room_id:
            where = "WHERE room_id = ?"
            params = (str(room_id),)
        elif player_uid:
            where = "WHERE player_uid = ?"
            params = (str(player_uid),)

        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM messages
                {where};
                """,
                params,
            ).fetchone()

        return int(row["n"])

    # ==================================================================
    # MAINTENANCE
    # ==================================================================

    def delete_conversation(
        self,
        *,
        room_id: str,
        delete_orphan_player: bool = False,
    ) -> bool:
        room_id = self._required_str(room_id, "room_id")

        with self._lock:
            row = self._conn.execute(
                """
                SELECT player_uid
                FROM conversations
                WHERE room_id = ?;
                """,
                (room_id,),
            ).fetchone()

            if row is None:
                return False

            player_uid = str(row["player_uid"])

            self._conn.execute(
                """
                DELETE FROM conversations
                WHERE room_id = ?;
                """,
                (room_id,),
            )

            if delete_orphan_player:
                remaining = self._conn.execute(
                    """
                    SELECT 1
                    FROM conversations
                    WHERE player_uid = ?
                    LIMIT 1;
                    """,
                    (player_uid,),
                ).fetchone()

                if remaining is None:
                    self._conn.execute(
                        """
                        DELETE FROM players
                        WHERE uid = ?;
                        """,
                        (player_uid,),
                    )

            self._conn.commit()

        return True

    # ==================================================================
    # INTERNAL UPSERTS
    # ==================================================================

    def _upsert_player_locked(
        self,
        *,
        uid: str,
        name: str | None,
        alliance: str | None,
        server_id: int | None,
        language: str | None,
        seen_at: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO players (
                uid,
                name,
                alliance,
                server_id,
                language,
                first_seen_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                name = COALESCE(excluded.name, players.name),
                alliance = COALESCE(excluded.alliance, players.alliance),
                server_id = COALESCE(excluded.server_id, players.server_id),
                language = COALESCE(excluded.language, players.language),
                last_seen_at = CASE
                    WHEN excluded.last_seen_at > players.last_seen_at
                    THEN excluded.last_seen_at
                    ELSE players.last_seen_at
                END;
            """,
            (
                uid,
                name,
                alliance,
                server_id,
                language,
                seen_at,
                seen_at,
            ),
        )

    def _upsert_conversation_locked(
        self,
        *,
        room_id: str,
        player_uid: str,
        group_name: str,
        seen_at: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO conversations (
                room_id,
                player_uid,
                group_name,
                first_seen_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE SET
                player_uid = excluded.player_uid,
                group_name = excluded.group_name,
                last_seen_at = CASE
                    WHEN excluded.last_seen_at > conversations.last_seen_at
                    THEN excluded.last_seen_at
                    ELSE conversations.last_seen_at
                END;
            """,
            (
                room_id,
                player_uid,
                group_name,
                seen_at,
                seen_at,
            ),
        )

    # ==================================================================
    # ROW CONVERTERS
    # ==================================================================

    @staticmethod
    def _row_to_message(
        row: sqlite3.Row,
    ) -> StoredMessage:
        raw_data = None
        raw_json = row["raw_data_json"]

        if raw_json:
            try:
                decoded = json.loads(raw_json)
                if isinstance(decoded, dict):
                    raw_data = decoded
            except json.JSONDecodeError:
                raw_data = None

        return StoredMessage(
            id=row["id"],
            room_id=row["room_id"],
            player_uid=row["player_uid"],
            direction=row["direction"],
            role=row["role"],
            text=row["text"],
            seq_id=row["seq_id"],
            source_message_key=row["source_message_key"],
            sender_uid=row["sender_uid"],
            sender_name=row["sender_name"],
            sender_alliance=row["sender_alliance"],
            sender_server_id=row["sender_server_id"],
            server_time=row["server_time"],
            send_time=row["send_time"],
            created_at=row["created_at"],
            message_type=row["message_type"],
            post_type=row["post_type"],
            original_lang=row["original_lang"],
            pushseq=row["pushseq"],
            raw_data=raw_data,
        )

    @staticmethod
    def _row_to_memory(
        row: sqlite3.Row,
    ) -> PlayerMemory:
        return PlayerMemory(
            id=row["id"],
            player_uid=row["player_uid"],
            memory_type=row["memory_type"],
            memory_key=row["memory_key"],
            content=row["content"],
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            active=bool(row["active"]),
            source_message_id=row["source_message_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
            expires_at=row["expires_at"],
        )

    @staticmethod
    def _row_to_summary(
        row: sqlite3.Row,
    ) -> ConversationSummary:
        return ConversationSummary(
            id=row["id"],
            room_id=row["room_id"],
            player_uid=row["player_uid"],
            summary=row["summary"],
            start_message_id=row["start_message_id"],
            end_message_id=row["end_message_id"],
            message_count=row["message_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ==================================================================
    # HELPERS
    # ==================================================================

    @staticmethod
    def _fallback_message_key(
        *,
        room_id: str,
        sender_uid: str,
        server_time: int | None,
        text: str,
    ) -> str:
        return (
            f"fallback:{room_id}:"
            f"{sender_uid}:"
            f"{server_time}:"
            f"{text}"
        )

    @staticmethod
    def _json_dumps(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        try:
            if is_dataclass(value):
                value = asdict(value)

            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return json.dumps(
                {"repr": repr(value)},
                ensure_ascii=False,
            )

    @staticmethod
    def _required_str(
        value: Any,
        name: str,
    ) -> str:
        if value is None:
            raise ValueError(f"{name} cannot be None.")

        result = str(value).strip()

        if not result:
            raise ValueError(f"{name} cannot be empty.")

        return result

    @staticmethod
    def _optional_str(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        value = str(value).strip()
        return value or None

    @staticmethod
    def _optional_int(
        value: Any,
    ) -> int | None:
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _score(
        value: Any,
        name: str,
    ) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name} must be a number from 0.0 to 1.0."
            ) from exc

        if not 0.0 <= result <= 1.0:
            raise ValueError(
                f"{name} must be between 0.0 and 1.0."
            )

        return result

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)


# ======================================================================
# MODULE HELPERS / SELF TEST
# ======================================================================


def _relationship_or_unknown(value: Any) -> Relationship:
    try:
        return Relationship(str(value or Relationship.UNKNOWN.value).upper())
    except ValueError:
        return Relationship.UNKNOWN


def _self_test() -> None:
    """
    No BlueStacks required.
    Creates conversation_store_test.db next to this file.
    """

    try:
        from archive.legacy_runtime.chat_capture import ChatMessage
    except ImportError as exc:
        raise SystemExit(
            "The archived ChatCapture module is required for the self-test."
        ) from exc

    test_db = Path(__file__).with_name(
        "conversation_store_test.db"
    )

    # Remove SQLite files from an older test.
    for candidate in (
        test_db,
        Path(str(test_db) + "-wal"),
        Path(str(test_db) + "-shm"),
    ):
        if candidate.exists():
            candidate.unlink()

    room_id = (
        "custom_1349515331000772_"
        "1224463480000772_v2"
    )
    uid = "1349515331000772"

    print("Test DB:", test_db)

    with ConversationStore(test_db) as store:
        incoming = ChatMessage(
            room_id=room_id,
            group="custom",
            sender_uid=uid,
            sender_name="OG doktorb",
            text="hello",
            seq_id=258,
            server_time=1791930738885,
            send_time=1791930738,
            sender_alliance="ROXY",
            sender_server_id=772,
            message_type=0,
            post_type=0,
            original_lang="it",
            pushseq="10000210000211979169917102903",
            raw_data={
                "msg": "hello",
                "group": "custom",
            },
        )

        print(
            "Incoming inserted:",
            store.save_incoming(incoming),
        )

        print(
            "Duplicate inserted:",
            store.save_incoming(incoming),
        )

        incoming_stored = store.get_message_by_source_key(
            incoming.source_message_key
        )

        outgoing_id = store.save_outgoing(
            room_id=room_id,
            player_uid=uid,
            text="Hey bro",
            sender_uid="1224463480000772",
            sender_name="PEva",
        )

        print("Outgoing ID:", outgoing_id)

        # Long-term facts.
        m1 = store.add_memory(
            player_uid=uid,
            memory_type="schedule",
            memory_key="work.schedule",
            content=(
                "Player often works during evening hours "
                "and may miss some alliance events."
            ),
            importance=0.85,
            confidence=0.90,
            source_message_id=(
                incoming_stored.id
                if incoming_stored
                else None
            ),
        )

        m2 = store.add_memory(
            player_uid=uid,
            memory_type="preference",
            memory_key="event.tyrant.preference",
            content="Player likes participating in Tyrant.",
            importance=0.70,
            confidence=0.80,
        )

        print("Memory IDs:", m1, m2)

        # Same memory_key updates instead of duplicating.
        m1_again = store.add_memory(
            player_uid=uid,
            memory_type="schedule",
            memory_key="work.schedule",
            content=(
                "Player often works late shifts and may "
                "miss evening alliance events."
            ),
            importance=0.90,
            confidence=0.95,
        )

        print(
            "Updated same memory ID:",
            m1_again == m1,
        )

        summary_id = store.save_summary(
            room_id=room_id,
            player_uid=uid,
            summary=(
                "Earlier conversations included Tyrant participation "
                "and the player's work schedule."
            ),
            start_message_id=(
                incoming_stored.id
                if incoming_stored
                else None
            ),
            end_message_id=outgoing_id,
            message_count=2,
        )

        print("Summary ID:", summary_id)

        context = store.get_ai_context(
            player_uid=uid,
            room_id=room_id,
            recent_history_limit=20,
            memory_limit=10,
        )

        print()
        print("Recent history:")
        for turn in context.history:
            print(f"  {turn.role}: {turn.content}")

        print()
        print("Long-term memories:")
        for memory in context.memories:
            print(" ", memory)

        print()
        print("Old conversation summary:")
        print(" ", context.summary)

        print()
        print("Player:")
        print(" ", context.player)

        print()
        print(
            "Message count:",
            store.message_count(room_id=room_id),
        )

    print()
    print("SELF-TEST OK")


if __name__ == "__main__":
    _self_test()
