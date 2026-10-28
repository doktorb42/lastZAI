"""SQLite repository owned exclusively by Python conversation code.

All SQL in this module is fixed source code plus bound parameters. Model output is
never used as SQL, a table/column name, a database path, or a transaction boundary.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .contracts import (
    CHAT_PROMPT_VERSION,
    CONTEXT_CONTRACT_VERSION,
    MAX_CONTEXT_CHARACTERS,
    MAX_LONG_TERM_MEMORIES,
    MAX_OPEN_THREADS,
    MAX_RECENT_MESSAGES,
    MAX_TEMPORARY_MEMORIES,
    ContractError,
    PrivateChatObservation,
    canonical_json,
    normalize_nickname,
    normalize_text,
    parse_utc_timestamp,
    render_chat_messages,
    sha256_json,
    sha256_text,
    utc_now,
    validate_identity_value,
)


# Version 4 adds the V2 routing boundary, immutable model-task snapshots, and
# explicit turn lifecycle timestamps.  This is deliberately an *additive*
# migration: old pilot databases remain readable, while every new V2 turn uses
# the safer tables/columns below.
SCHEMA_VERSION = "4"


class RepositoryError(RuntimeError):
    """The durable conversation state cannot safely satisfy a request."""


class IdentityNotConfirmedError(RepositoryError):
    """An observation could not be attached to a verified stable player."""


class TurnStateError(RepositoryError):
    """A requested operation conflicts with the durable turn state."""


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    primary_nickname TEXT NOT NULL,
    language TEXT,
    alliance_name TEXT,
    alliance_role TEXT,
    relationship_style TEXT,
    identity_status TEXT NOT NULL CHECK(identity_status IN ('verified','provisional','blocked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_identity_bindings (
    binding_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    identity_type TEXT NOT NULL CHECK(identity_type IN ('game_player_id','operator_binding')),
    identity_value TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)),
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(identity_type, identity_value)
);

CREATE TABLE IF NOT EXISTS player_aliases (
    alias_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    nickname TEXT NOT NULL,
    nickname_normalized TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(player_id, nickname_normalized)
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL UNIQUE REFERENCES players(player_id) ON DELETE RESTRICT,
    channel TEXT NOT NULL CHECK(channel = 'private'),
    first_rollout_index INTEGER NOT NULL CHECK(first_rollout_index = 0),
    status TEXT NOT NULL CHECK(status IN ('active','paused','closed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Real Last Z routing is durable runtime metadata.  It is intentionally kept
-- in a table separate from context_snapshots, whose JSON is model-facing.
CREATE TABLE IF NOT EXISTS external_conversation_routes (
    route_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    external_room_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('private')),
    counterpart_game_player_id TEXT NOT NULL,
    counterpart_binding_id TEXT REFERENCES player_identity_bindings(binding_id) ON DELETE RESTRICT,
    peva_game_player_id TEXT,
    verification_state TEXT NOT NULL CHECK(verification_state IN ('pending','verified','rejected')),
    verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, external_room_id),
    UNIQUE(conversation_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_external_routes_counterpart
    ON external_conversation_routes(counterpart_game_player_id, verification_state);

-- Binding changes are rare, but must be auditable and must not rewrite the
-- evidence that justified an earlier captured turn.
CREATE TABLE IF NOT EXISTS external_route_revisions (
    revision_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES external_conversation_routes(route_id) ON DELETE RESTRICT,
    previous_external_room_id TEXT NOT NULL,
    previous_channel TEXT NOT NULL,
    previous_counterpart_game_player_id TEXT NOT NULL,
    previous_peva_game_player_id TEXT,
    previous_verification_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    revised_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    direction TEXT NOT NULL CHECK(direction IN ('incoming','outgoing')),
    source_message_id TEXT,
    source_message_key TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, direction, source_message_key)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_observed
    ON messages(conversation_id, observed_at, created_at);

-- Every capture observation is retained, including idempotent re-observations.
-- The immutable messages row is never rewritten by a duplicate ingest.
CREATE TABLE IF NOT EXISTS message_ingest_revisions (
    ingest_revision_id TEXT PRIMARY KEY,
    message_id TEXT REFERENCES messages(message_id) ON DELETE RESTRICT,
    conversation_id TEXT REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    source_message_key TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('created','duplicate','quarantined')),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_message_ingest_revisions_message
    ON message_ingest_revisions(message_id, created_at);

CREATE TABLE IF NOT EXISTS conversation_cursors (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    last_seen_message_id TEXT REFERENCES messages(message_id) ON DELETE RESTRICT,
    last_ingested_message_id TEXT REFERENCES messages(message_id) ON DELETE RESTRICT,
    last_processed_message_id TEXT REFERENCES messages(message_id) ON DELETE RESTRICT,
    last_reply_generated_message_id TEXT REFERENCES messages(message_id) ON DELETE RESTRICT,
    last_approved_message_id TEXT REFERENCES messages(message_id) ON DELETE RESTRICT,
    last_delivered_message_id TEXT REFERENCES messages(message_id) ON DELETE RESTRICT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_items (
    memory_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('fact','preference','relationship','game_context','communication_style')),
    memory_key TEXT NOT NULL,
    value_text TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('long_term','temporary')),
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    expires_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('active','superseded','expired','retracted')),
    evidence_message_ids_json TEXT NOT NULL,
    created_from_attempt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_items_active
    ON memory_items(player_id, status, scope, expires_at);

CREATE TABLE IF NOT EXISTS memory_revisions (
    revision_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memory_items(memory_id) ON DELETE RESTRICT,
    previous_value_text TEXT NOT NULL,
    previous_confidence REAL NOT NULL,
    previous_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    attempt_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_field_confidence (
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    field_name TEXT NOT NULL CHECK(field_name IN ('language','alliance','alliance_role','relationship_style')),
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    evidence_message_ids_json TEXT NOT NULL,
    updated_from_attempt_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(player_id, field_name)
);

CREATE TABLE IF NOT EXISTS conversation_threads (
    thread_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    promise_text TEXT NOT NULL,
    due_at TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL CHECK(status IN ('open','closed','expired')),
    evidence_message_ids_json TEXT NOT NULL,
    created_from_attempt_id TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    close_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_threads_active
    ON conversation_threads(conversation_id, status, due_at);

CREATE TABLE IF NOT EXISTS pending_promises (
    promise_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    promise_text TEXT NOT NULL,
    due_at TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL CHECK(status IN ('open','closed','expired')),
    evidence_message_ids_json TEXT NOT NULL,
    created_from_attempt_id TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    close_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_promises_active
    ON pending_promises(conversation_id, status, due_at);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    summary_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    summary_text TEXT NOT NULL,
    covers_through_message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE RESTRICT,
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL CHECK(status IN ('active','superseded')),
    evidence_message_ids_json TEXT NOT NULL,
    created_from_attempt_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_summaries_active
    ON conversation_summaries(conversation_id, status, created_at);

CREATE TABLE IF NOT EXISTS context_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    contract_version TEXT NOT NULL,
    context_json TEXT NOT NULL,
    context_sha256 TEXT NOT NULL,
    context_truncated INTEGER NOT NULL CHECK(context_truncated IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, context_sha256)
);

-- Alias mappings are storage-only.  The canonical context JSON contains only
-- p0/c0/m0/mem0/thread0/promise0-style aliases, never these entity IDs.
CREATE TABLE IF NOT EXISTS context_snapshot_aliases (
    snapshot_id TEXT NOT NULL REFERENCES context_snapshots(snapshot_id) ON DELETE RESTRICT,
    alias TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('player','conversation','message','memory','thread','promise','summary')),
    entity_id TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, alias),
    UNIQUE(snapshot_id, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_context_snapshot_alias_entity
    ON context_snapshot_aliases(snapshot_id, entity_type, entity_id);

CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
    player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE RESTRICT,
    incoming_message_id TEXT NOT NULL UNIQUE REFERENCES messages(message_id) ON DELETE RESTRICT,
    snapshot_id TEXT NOT NULL REFERENCES context_snapshots(snapshot_id) ON DELETE RESTRICT,
    chat_task_snapshot_id TEXT,
    chat_prompt_version TEXT NOT NULL,
    chat_messages_json TEXT NOT NULL,
    generated_reply_raw TEXT,
    generated_reply_normalized TEXT,
    player_ready_validation_json TEXT,
    generation_status TEXT NOT NULL CHECK(generation_status IN ('awaiting_generation','generated','generation_rejected','generation_error')),
    edited_reply TEXT,
    approved_reply TEXT,
    approval_status TEXT NOT NULL CHECK(approval_status IN ('pending','approved','rejected')),
    reviewer TEXT,
    reviewed_at TEXT,
    model_provenance_json TEXT,
    memory_status TEXT NOT NULL CHECK(memory_status IN ('not_requested','proposed','validated','rejected','applied')),
    delivery_status TEXT NOT NULL CHECK(delivery_status IN ('not_delivered','delivered','delivery_failed')),
    outgoing_message_id TEXT REFERENCES messages(message_id) ON DELETE RESTRICT,
    ingested_at TEXT,
    reply_generated_at TEXT,
    approved_at TEXT,
    processed_at TEXT,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_turns_export
    ON chat_turns(approval_status, memory_status, created_at);

-- A task snapshot is the exact immutable input sent to a model: canonical task
-- input plus rendered chat messages.  It is not regenerated from mutable
-- player state during export, validation, or a retry.
CREATE TABLE IF NOT EXISTS task_snapshots (
    task_snapshot_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES chat_turns(turn_id) ON DELETE RESTRICT,
    context_snapshot_id TEXT NOT NULL REFERENCES context_snapshots(snapshot_id) ON DELETE RESTRICT,
    task_kind TEXT NOT NULL CHECK(task_kind IN ('chat','memory')),
    task_tag TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    context_contract_version TEXT NOT NULL,
    input_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    messages_json TEXT NOT NULL,
    messages_sha256 TEXT NOT NULL,
    allowed_refs_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(turn_id, task_kind)
);

CREATE INDEX IF NOT EXISTS idx_task_snapshots_turn
    ON task_snapshots(turn_id, task_kind);

-- Append-only lifecycle evidence makes the important state distinction
-- inspectable without inferring it from nullable columns.
CREATE TABLE IF NOT EXISTS turn_lifecycle_events (
    lifecycle_event_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES chat_turns(turn_id) ON DELETE RESTRICT,
    incoming_message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK(state IN ('ingested','reply_generated','approved','processed','delivered')),
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turn_lifecycle_events_turn
    ON turn_lifecycle_events(turn_id, created_at);

CREATE TABLE IF NOT EXISTS memory_update_attempts (
    attempt_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES chat_turns(turn_id) ON DELETE RESTRICT,
    task_snapshot_id TEXT,
    memory_prompt_version TEXT NOT NULL,
    memory_messages_json TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    parsed_json TEXT,
    validation_json TEXT NOT NULL,
    model_provenance_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('validated','rejected','applied')),
    applied_by TEXT,
    applied_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_attempts_turn
    ON memory_update_attempts(turn_id, created_at);

CREATE TABLE IF NOT EXISTS quarantined_observations (
    quarantine_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _uuid() -> str:
    return str(uuid.uuid4())


def _json_value(raw: str | None, default: Any = None) -> Any:
    if raw is None:
        return default
    return json.loads(raw)


def _optional_text(value: str | None, maximum: int = 160) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RepositoryError("optional profile values must be strings")
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise RepositoryError(f"profile value exceeds {maximum} characters")
    return normalized


def _private_channel(value: Any) -> str:
    """Validate the deliberately narrow first-rollout routing channel."""

    if value != "private":
        raise RepositoryError("only the private Last Z channel is supported in this rollout")
    return "private"


def _observation_routing_value(observation: PrivateChatObservation, name: str, default: Any = None) -> Any:
    """Read V2 ingress fields while retaining compatibility with pilot captures."""

    return getattr(observation, name, default)


class ConversationRepository:
    """Durable store with a fixed schema and parameterized writes only."""

    def __init__(self, database_path: Path | str):
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.initialize()

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(_SCHEMA_SQL)
        self._migrate_existing_schema()
        self.connection.execute(
            "INSERT INTO schema_meta(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            ("schema_version", SCHEMA_VERSION, utc_now()),
        )

    def _migrate_existing_schema(self) -> None:
        """Apply additive pilot -> PEva V2 migrations without rewriting evidence.

        The old `last_processed_message_id` was set at ingestion.  That did not
        mean memory processing had actually happened, so a pre-V2 database must
        not be silently reinterpreted as proof of processing.  We preserve the
        old rows but clear that ambiguous cursor on its one-time upgrade.
        """

        previous = self.connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        previous_version = str(previous["value"]) if previous is not None else None

        def columns_for(table: str) -> set[str]:
            return {
                str(row["name"])
                for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
            }

        chat_columns = columns_for("chat_turns")
        additions = {
            "delivery_status": "TEXT NOT NULL DEFAULT 'not_delivered'",
            "outgoing_message_id": "TEXT",
            "ingested_at": "TEXT",
            "reply_generated_at": "TEXT",
            "approved_at": "TEXT",
            "processed_at": "TEXT",
            "delivered_at": "TEXT",
            "chat_task_snapshot_id": "TEXT",
        }
        for name, definition in additions.items():
            if name not in chat_columns:
                self.connection.execute(f"ALTER TABLE chat_turns ADD COLUMN {name} {definition}")

        cursor_columns = columns_for("conversation_cursors")
        for name in (
            "last_ingested_message_id",
            "last_reply_generated_message_id",
            "last_approved_message_id",
            "last_delivered_message_id",
        ):
            if name not in cursor_columns:
                self.connection.execute(f"ALTER TABLE conversation_cursors ADD COLUMN {name} TEXT")

        attempt_columns = columns_for("memory_update_attempts")
        if "model_provenance_json" not in attempt_columns:
            self.connection.execute("ALTER TABLE memory_update_attempts ADD COLUMN model_provenance_json TEXT")
        if "task_snapshot_id" not in attempt_columns:
            self.connection.execute("ALTER TABLE memory_update_attempts ADD COLUMN task_snapshot_id TEXT")

        if previous_version is not None and previous_version != SCHEMA_VERSION:
            # Existing data retains its raw evidence.  We only establish honest
            # lifecycle timestamps when the old durable state proves them.
            self.connection.execute(
                "UPDATE chat_turns SET ingested_at=COALESCE(ingested_at, created_at), "
                "reply_generated_at=CASE WHEN reply_generated_at IS NULL "
                "AND generation_status IN ('generated','generation_rejected') THEN updated_at ELSE reply_generated_at END, "
                "approved_at=CASE WHEN approved_at IS NULL AND approval_status='approved' "
                "THEN reviewed_at ELSE approved_at END, "
                "processed_at=CASE WHEN processed_at IS NULL AND memory_status='applied' "
                "THEN updated_at ELSE processed_at END"
            )
            self.connection.execute(
                "UPDATE conversation_cursors SET last_ingested_message_id=COALESCE(last_ingested_message_id, last_seen_message_id), "
                "last_processed_message_id=NULL, last_reply_generated_message_id=NULL, "
                "last_approved_message_id=NULL, last_delivered_message_id=NULL"
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def enroll_verified_player(
        self,
        *,
        nickname: str,
        identity_type: str,
        identity_value: str,
        player_id: str | None = None,
        language: str | None = None,
        alliance_name: str | None = None,
        alliance_role: str | None = None,
        relationship_style: str | None = None,
        external_room_id: str | None = None,
        room_id: str | None = None,
        channel: str = "private",
        counterpart_game_player_id: str | None = None,
        counterpart_uid: str | None = None,
        peva_game_player_id: str | None = None,
        verification_state: str = "verified",
    ) -> dict[str, str]:
        """Create an operator-confirmed identity binding and private conversation."""

        if identity_type not in {"game_player_id", "operator_binding"}:
            raise RepositoryError("identity_type must be game_player_id or operator_binding")
        canonical_nickname = " ".join(nickname.strip().split())
        nickname_normalized = normalize_nickname(canonical_nickname)
        identity_value = validate_identity_value(identity_value)
        player_id = player_id or _uuid()
        now = utc_now()
        with self.transaction():
            existing = self.connection.execute(
                "SELECT player_id FROM player_identity_bindings WHERE identity_type=? AND identity_value=?",
                (identity_type, identity_value),
            ).fetchone()
            if existing is not None and existing["player_id"] != player_id:
                raise RepositoryError("identity binding already belongs to a different player")
            self.connection.execute(
                "INSERT INTO players(player_id, primary_nickname, language, alliance_name, alliance_role, relationship_style, identity_status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?) "
                "ON CONFLICT(player_id) DO UPDATE SET primary_nickname=excluded.primary_nickname, "
                "language=COALESCE(excluded.language, players.language), "
                "alliance_name=COALESCE(excluded.alliance_name, players.alliance_name), "
                "alliance_role=COALESCE(excluded.alliance_role, players.alliance_role), "
                "relationship_style=COALESCE(excluded.relationship_style, players.relationship_style), "
                "identity_status='verified', updated_at=excluded.updated_at",
                (
                    player_id,
                    canonical_nickname,
                    _optional_text(language, 32),
                    _optional_text(alliance_name),
                    _optional_text(alliance_role),
                    _optional_text(relationship_style),
                    now,
                    now,
                ),
            )
            self.connection.execute(
                "INSERT INTO player_identity_bindings(binding_id, player_id, identity_type, identity_value, confidence, confirmed, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, 1.0, 1, ?, ?) "
                "ON CONFLICT(identity_type, identity_value) DO UPDATE SET player_id=excluded.player_id, "
                "confidence=1.0, confirmed=1, last_seen_at=excluded.last_seen_at",
                (_uuid(), player_id, identity_type, identity_value, now, now),
            )
            self.connection.execute(
                "INSERT INTO player_aliases(alias_id, player_id, nickname, nickname_normalized, confidence, active, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, 1.0, 1, ?, ?) "
                "ON CONFLICT(player_id, nickname_normalized) DO UPDATE SET nickname=excluded.nickname, "
                "active=1, last_seen_at=excluded.last_seen_at",
                (_uuid(), player_id, canonical_nickname, nickname_normalized, now, now),
            )
            conversation_id = self._ensure_conversation(player_id, now)

        # Route binding is optional for legacy pilot captures, but once an
        # external room is supplied it must be anchored to a verified game UID.
        route_id: str | None = None
        if external_room_id is not None and room_id is not None and external_room_id != room_id:
            raise RepositoryError("external_room_id and room_id disagree")
        if counterpart_game_player_id is not None and counterpart_uid is not None and counterpart_game_player_id != counterpart_uid:
            raise RepositoryError("counterpart_game_player_id and counterpart_uid disagree")
        route_room_id = external_room_id if external_room_id is not None else room_id
        route_counterpart_uid = (
            counterpart_game_player_id if counterpart_game_player_id is not None else counterpart_uid
        )
        if route_room_id is not None:
            route = self.bind_external_route(
                conversation_id=conversation_id,
                external_room_id=route_room_id,
                channel=channel,
                counterpart_game_player_id=(
                    route_counterpart_uid
                    if route_counterpart_uid is not None
                    else (identity_value if identity_type == "game_player_id" else None)
                ),
                peva_game_player_id=peva_game_player_id,
                verification_state=verification_state,
                reason="initial_verified_enrollment",
            )
            route_id = str(route["route_id"])
        result = {"player_id": player_id, "conversation_id": conversation_id}
        if route_id is not None:
            result["route_id"] = route_id
        return result

    def _ensure_conversation(self, player_id: str, now: str) -> str:
        row = self.connection.execute(
            "SELECT conversation_id FROM conversations WHERE player_id=?",
            (player_id,),
        ).fetchone()
        if row is not None:
            return str(row["conversation_id"])
        conversation_id = _uuid()
        self.connection.execute(
            "INSERT INTO conversations(conversation_id, player_id, channel, first_rollout_index, status, created_at, updated_at) "
            "VALUES (?, ?, 'private', 0, 'active', ?, ?)",
            (conversation_id, player_id, now, now),
        )
        self.connection.execute(
            "INSERT INTO conversation_cursors(conversation_id, last_seen_message_id, last_ingested_message_id, "
            "last_processed_message_id, last_reply_generated_message_id, last_approved_message_id, "
            "last_delivered_message_id, updated_at) VALUES (?, NULL, NULL, NULL, NULL, NULL, NULL, ?)",
            (conversation_id, now),
        )
        return conversation_id

    def bind_external_route(
        self,
        *,
        conversation_id: str,
        external_room_id: str | None = None,
        room_id: str | None = None,
        channel: str = "private",
        counterpart_game_player_id: str | None = None,
        counterpart_uid: str | None = None,
        peva_game_player_id: str | None = None,
        verification_state: str = "verified",
        reason: str = "operator_verified_binding",
    ) -> dict[str, Any]:
        """Bind a real Last Z route without ever placing it in model context.

        A verified route must point to a confirmed `game_player_id` binding for
        this conversation's player.  A caller may store a pending/rejected
        route for audit purposes, but it cannot ingest a model-facing turn.
        """

        if external_room_id is not None and room_id is not None and external_room_id != room_id:
            raise RepositoryError("external_room_id and room_id disagree")
        route_value = external_room_id if external_room_id is not None else room_id
        if route_value is None:
            raise RepositoryError("external_room_id is required for an external route")
        if counterpart_game_player_id is not None and counterpart_uid is not None and counterpart_game_player_id != counterpart_uid:
            raise RepositoryError("counterpart_game_player_id and counterpart_uid disagree")
        route_room = validate_identity_value(route_value)
        route_channel = _private_channel(channel)
        counterpart_value = counterpart_game_player_id if counterpart_game_player_id is not None else counterpart_uid
        if counterpart_value is None:
            raise RepositoryError("counterpart_game_player_id is required for an external route")
        counterpart_uid = validate_identity_value(counterpart_value)
        peva_uid = validate_identity_value(peva_game_player_id) if peva_game_player_id is not None else None
        if verification_state not in {"pending", "verified", "rejected"}:
            raise RepositoryError("verification_state must be pending, verified, or rejected")
        reason = _optional_text(reason, 200) or "operator_verified_binding"
        now = utc_now()
        with self.transaction():
            conversation = self.connection.execute(
                "SELECT c.conversation_id, c.player_id FROM conversations c WHERE c.conversation_id=?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise RepositoryError("conversation not found for external route")
            binding = self.connection.execute(
                "SELECT binding_id, confirmed FROM player_identity_bindings "
                "WHERE player_id=? AND identity_type='game_player_id' AND identity_value=?",
                (conversation["player_id"], counterpart_uid),
            ).fetchone()
            if verification_state == "verified" and (binding is None or int(binding["confirmed"]) != 1):
                raise IdentityNotConfirmedError(
                    "a verified external route requires a confirmed game_player_id binding for the player"
                )

            other_route = self.connection.execute(
                "SELECT route_id, conversation_id FROM external_conversation_routes "
                "WHERE channel=? AND external_room_id=?",
                (route_channel, route_room),
            ).fetchone()
            if other_route is not None and str(other_route["conversation_id"]) != conversation_id:
                raise RepositoryError("external room is already bound to a different conversation")

            existing = self.connection.execute(
                "SELECT * FROM external_conversation_routes WHERE conversation_id=? AND channel=?",
                (conversation_id, route_channel),
            ).fetchone()
            binding_id = str(binding["binding_id"]) if binding is not None else None
            if existing is None:
                route_id = _uuid()
                self.connection.execute(
                    "INSERT INTO external_conversation_routes(route_id, conversation_id, external_room_id, channel, "
                    "counterpart_game_player_id, counterpart_binding_id, peva_game_player_id, verification_state, "
                    "verified_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        route_id,
                        conversation_id,
                        route_room,
                        route_channel,
                        counterpart_uid,
                        binding_id,
                        peva_uid,
                        verification_state,
                        now if verification_state == "verified" else None,
                        now,
                        now,
                    ),
                )
            else:
                route_id = str(existing["route_id"])
                same = (
                    str(existing["external_room_id"]) == route_room
                    and str(existing["counterpart_game_player_id"]) == counterpart_uid
                    and existing["peva_game_player_id"] == peva_uid
                    and str(existing["verification_state"]) == verification_state
                    and existing["counterpart_binding_id"] == binding_id
                )
                if not same:
                    self.connection.execute(
                        "INSERT INTO external_route_revisions(revision_id, route_id, previous_external_room_id, "
                        "previous_channel, previous_counterpart_game_player_id, previous_peva_game_player_id, "
                        "previous_verification_state, reason, revised_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            _uuid(),
                            route_id,
                            existing["external_room_id"],
                            existing["channel"],
                            existing["counterpart_game_player_id"],
                            existing["peva_game_player_id"],
                            existing["verification_state"],
                            reason,
                            now,
                        ),
                    )
                    self.connection.execute(
                        "UPDATE external_conversation_routes SET external_room_id=?, counterpart_game_player_id=?, "
                        "counterpart_binding_id=?, peva_game_player_id=?, verification_state=?, "
                        "verified_at=?, updated_at=? WHERE route_id=?",
                        (
                            route_room,
                            counterpart_uid,
                            binding_id,
                            peva_uid,
                            verification_state,
                            now if verification_state == "verified" else None,
                            now,
                            route_id,
                        ),
                    )
            route = self.connection.execute(
                "SELECT * FROM external_conversation_routes WHERE route_id=?", (route_id,)
            ).fetchone()
        return dict(route) if route is not None else {"route_id": route_id}

    def external_route_for_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        """Return runtime-only routing data; callers must never pass it to a model."""

        row = self.connection.execute(
            "SELECT * FROM external_conversation_routes WHERE conversation_id=? AND channel='private'",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def external_route_for_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT r.* FROM external_conversation_routes r JOIN chat_turns t ON t.conversation_id=r.conversation_id "
            "WHERE t.turn_id=? AND r.channel='private'",
            (turn_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def runtime_identifiers_for_turn(self, turn_id: str) -> set[str]:
        """Return private identifiers that must never re-enter model text.

        This is intentionally a storage-only guard, not prompt data.  The
        aliases in a task snapshot cannot reveal these values, but checking a
        model completion against the values prevents accidental echoing from a
        compromised client, stale prompt cache, or malformed caller.
        """

        row = self.connection.execute(
            "SELECT t.turn_id, t.player_id, t.conversation_id, t.snapshot_id, t.incoming_message_id, "
            "r.route_id, r.external_room_id, r.counterpart_game_player_id, r.peva_game_player_id "
            "FROM chat_turns t LEFT JOIN external_conversation_routes r "
            "ON r.conversation_id=t.conversation_id AND r.channel='private' WHERE t.turn_id=?",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("turn not found")
        values = {str(value) for value in dict(row).values() if isinstance(value, str) and len(value) >= 3}
        message_rows = self.connection.execute(
            "SELECT source_message_id, source_message_key FROM messages WHERE conversation_id=?",
            (row["conversation_id"],),
        ).fetchall()
        for message in message_rows:
            for value in (message["source_message_id"], message["source_message_key"]):
                if isinstance(value, str) and len(value) >= 3:
                    values.add(value)
        return values

    def _verified_player_for_observation(self, observation: PrivateChatObservation) -> sqlite3.Row | None:
        row = self.connection.execute(
            "SELECT p.*, b.binding_id FROM players p "
            "JOIN player_identity_bindings b ON b.player_id=p.player_id "
            "WHERE b.identity_type=? AND b.identity_value=? AND b.confirmed=1 AND p.identity_status='verified'",
            (observation.identity_type, observation.identity_value),
        ).fetchone()
        if row is None:
            return None
        nickname_normalized = normalize_nickname(observation.nickname)
        alias = self.connection.execute(
            "SELECT 1 FROM player_aliases WHERE player_id=? AND nickname_normalized=? AND active=1",
            (row["player_id"], nickname_normalized),
        ).fetchone()
        return row if alias is not None else None

    def _verified_route_for_observation(
        self,
        *,
        conversation_id: str,
        observation: PrivateChatObservation,
    ) -> tuple[sqlite3.Row | None, str | None]:
        """Verify optional V2 ingress routing against the durable binding.

        Legacy pilot observations did not carry a room ID, so they remain
        capturable for migration/testing.  As soon as an adapter supplies a
        room ID, however, it must match a verified route exactly; a nickname or
        person index is never sufficient routing authority.
        """

        external_room_id = _observation_routing_value(observation, "external_room_id")
        if external_room_id is None:
            return None, None
        try:
            room_id = validate_identity_value(external_room_id)
            channel = _private_channel(_observation_routing_value(observation, "channel", "private"))
        except (ContractError, RepositoryError) as exc:
            return None, f"invalid_external_route:{exc}"
        route = self.connection.execute(
            "SELECT * FROM external_conversation_routes WHERE conversation_id=? AND channel=? AND external_room_id=?",
            (conversation_id, channel, room_id),
        ).fetchone()
        if route is None:
            return None, "unbound_external_route"
        if str(route["verification_state"]) != "verified":
            return None, "external_route_not_verified"
        if observation.identity_type == "game_player_id" and observation.identity_value != route["counterpart_game_player_id"]:
            return None, "external_route_counterpart_uid_mismatch"
        observed_peva_uid = _observation_routing_value(observation, "peva_game_player_id")
        if observed_peva_uid is not None:
            try:
                observed_peva_uid = validate_identity_value(observed_peva_uid)
            except ContractError:
                return None, "invalid_peva_game_player_id"
            if route["peva_game_player_id"] is not None and observed_peva_uid != route["peva_game_player_id"]:
                return None, "external_route_peva_uid_mismatch"
        observed_state = _observation_routing_value(observation, "verification_state")
        if observed_state is not None and observed_state != "verified":
            return None, "observation_route_not_verified"
        return route, None

    def _record_ingest_revision(
        self,
        *,
        message_id: str | None,
        conversation_id: str | None,
        observation: PrivateChatObservation,
        outcome: str,
        created_at: str,
    ) -> None:
        """Keep an immutable capture audit trail, including idempotent retries."""

        self.connection.execute(
            "INSERT INTO message_ingest_revisions(ingest_revision_id, message_id, conversation_id, "
            "source_message_key, observation_json, outcome, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _uuid(),
                message_id,
                conversation_id,
                observation.source_message_key.strip(),
                canonical_json(observation.__dict__),
                outcome,
                created_at,
            ),
        )

    def quarantine_observation(self, observation: PrivateChatObservation, reason: str) -> str:
        observation.validate()
        quarantine_id = _uuid()
        self.connection.execute(
            "INSERT INTO quarantined_observations(quarantine_id, reason, observation_json, created_at) VALUES (?, ?, ?, ?)",
            (quarantine_id, reason, canonical_json(observation.__dict__), utc_now()),
        )
        return quarantine_id

    def ingest_observation(self, observation: PrivateChatObservation) -> dict[str, Any]:
        """Persist one new index-0 message and an immutable context snapshot.

        Unknown or nickname-mismatched identities are quarantined rather than
        attached to a memory record. This method never calls a model or game API.
        """

        observation.validate()
        now = utc_now()
        with self.transaction():
            player = self._verified_player_for_observation(observation)
            if player is None:
                quarantine_id = _uuid()
                self.connection.execute(
                    "INSERT INTO quarantined_observations(quarantine_id, reason, observation_json, created_at) VALUES (?, ?, ?, ?)",
                    (
                        quarantine_id,
                        "unverified_or_nickname_mismatched_identity",
                        canonical_json(observation.__dict__),
                        now,
                    ),
                )
                self._record_ingest_revision(
                    message_id=None,
                    conversation_id=None,
                    observation=observation,
                    outcome="quarantined",
                    created_at=now,
                )
                return {"status": "quarantined", "quarantine_id": quarantine_id}

            player_id = str(player["player_id"])
            conversation_id = self._ensure_conversation(player_id, now)
            _route, route_error = self._verified_route_for_observation(
                conversation_id=conversation_id,
                observation=observation,
            )
            if route_error is not None:
                quarantine_id = _uuid()
                self.connection.execute(
                    "INSERT INTO quarantined_observations(quarantine_id, reason, observation_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (quarantine_id, route_error, canonical_json(observation.__dict__), now),
                )
                self._record_ingest_revision(
                    message_id=None,
                    conversation_id=conversation_id,
                    observation=observation,
                    outcome="quarantined",
                    created_at=now,
                )
                return {"status": "quarantined", "quarantine_id": quarantine_id, "reason": route_error}
            duplicate = self.connection.execute(
                "SELECT m.message_id, t.turn_id FROM messages m "
                "LEFT JOIN chat_turns t ON t.incoming_message_id=m.message_id "
                "WHERE m.conversation_id=? AND m.direction='incoming' AND m.source_message_key=?",
                (conversation_id, observation.source_message_key.strip()),
            ).fetchone()
            if duplicate is not None:
                self._record_ingest_revision(
                    message_id=str(duplicate["message_id"]),
                    conversation_id=conversation_id,
                    observation=observation,
                    outcome="duplicate",
                    created_at=now,
                )
                return {
                    "status": "duplicate",
                    "player_id": player_id,
                    "conversation_id": conversation_id,
                    "message_id": str(duplicate["message_id"]),
                    "turn_id": str(duplicate["turn_id"]) if duplicate["turn_id"] else None,
                }

            message_id = _uuid()
            raw_text = observation.incoming_text.strip()
            normalized_text = normalize_text(raw_text)
            self.connection.execute(
                "INSERT INTO messages(message_id, conversation_id, player_id, direction, source_message_id, source_message_key, raw_text, normalized_text, content_sha256, observed_at, created_at) "
                "VALUES (?, ?, ?, 'incoming', ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    conversation_id,
                    player_id,
                    observation.source_message_id,
                    observation.source_message_key.strip(),
                    raw_text,
                    normalized_text,
                    sha256_text(normalized_text),
                    observation.observed_at,
                    now,
                ),
            )
            self._record_ingest_revision(
                message_id=message_id,
                conversation_id=conversation_id,
                observation=observation,
                outcome="created",
                created_at=now,
            )
            context, snapshot_aliases, context_truncated = self._build_context(
                conversation_id=conversation_id,
                player_id=player_id,
                player=player,
                incoming_message_id=message_id,
            )
            snapshot_id = _uuid()
            context_payload = canonical_json(context)
            self.connection.execute(
                "INSERT INTO context_snapshots(snapshot_id, conversation_id, player_id, contract_version, context_json, context_sha256, context_truncated, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    conversation_id,
                    player_id,
                    CONTEXT_CONTRACT_VERSION,
                    context_payload,
                    sha256_text(context_payload),
                    int(context_truncated),
                    now,
                ),
            )
            turn_id = _uuid()
            chat_messages = render_chat_messages(context)
            self.connection.execute(
                "INSERT INTO chat_turns(turn_id, conversation_id, player_id, incoming_message_id, snapshot_id, chat_prompt_version, "
                "chat_messages_json, generation_status, approval_status, memory_status, delivery_status, ingested_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_generation', 'pending', 'not_requested', 'not_delivered', ?, ?, ?)",
                (
                    turn_id,
                    conversation_id,
                    player_id,
                    message_id,
                    snapshot_id,
                    CHAT_PROMPT_VERSION,
                    canonical_json(chat_messages),
                    now,
                    now,
                    now,
                ),
            )
            self._store_context_aliases(snapshot_id=snapshot_id, aliases=snapshot_aliases)
            chat_task_snapshot_id = self._create_task_snapshot(
                turn_id=turn_id,
                context_snapshot_id=snapshot_id,
                task_kind="chat",
                context=context,
                messages=chat_messages,
                allowed_refs=None,
            )
            self.connection.execute(
                "UPDATE chat_turns SET chat_task_snapshot_id=? WHERE turn_id=?",
                (chat_task_snapshot_id, turn_id),
            )
            self._record_lifecycle_event(
                turn_id=turn_id,
                incoming_message_id=message_id,
                state="ingested",
                detail={"source": "capture"},
                created_at=now,
            )
            self.connection.execute(
                "UPDATE conversation_cursors SET last_seen_message_id=?, last_ingested_message_id=?, updated_at=? WHERE conversation_id=?",
                (message_id, message_id, now, conversation_id),
            )
            self.connection.execute(
                "UPDATE player_identity_bindings SET last_seen_at=? WHERE player_id=? AND identity_type=? AND identity_value=?",
                (now, player_id, observation.identity_type, observation.identity_value),
            )
            self.connection.execute(
                "UPDATE player_aliases SET last_seen_at=? WHERE player_id=? AND nickname_normalized=?",
                (now, player_id, normalize_nickname(observation.nickname)),
            )
        return {
            "status": "created",
            "player_id": player_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "turn_id": turn_id,
            "snapshot_id": snapshot_id,
            "context_truncated": context_truncated,
        }

    def _build_context(
        self,
        *,
        conversation_id: str,
        player_id: str,
        player: sqlite3.Row,
        incoming_message_id: str,
    ) -> tuple[dict[str, Any], list[tuple[str, str, str]], bool]:
        """Create a V2 model context and its private alias-to-entity mapping.

        The returned JSON can be persisted verbatim in `context_snapshots`.
        Internal UUIDs are deliberately returned separately for the repository,
        not embedded as hidden convenience fields in the model-facing object.
        """

        now = utc_now()
        self.connection.execute(
            "UPDATE memory_items SET status='expired', updated_at=? "
            "WHERE player_id=? AND status='active' AND scope='temporary' AND expires_at IS NOT NULL AND expires_at<=?",
            (now, player_id, now),
        )
        self.connection.execute(
            "UPDATE conversation_threads SET status='expired', closed_at=?, close_reason='due date elapsed' "
            "WHERE conversation_id=? AND status='open' AND due_at IS NOT NULL AND due_at<=?",
            (now, conversation_id, now),
        )
        self.connection.execute(
            "UPDATE pending_promises SET status='expired', closed_at=?, close_reason='due date elapsed' "
            "WHERE conversation_id=? AND status='open' AND due_at IS NOT NULL AND due_at<=?",
            (now, conversation_id, now),
        )
        incoming = self.connection.execute(
            "SELECT message_id, raw_text, observed_at FROM messages WHERE message_id=?",
            (incoming_message_id,),
        ).fetchone()
        if incoming is None:
            raise RepositoryError("incoming message disappeared before its snapshot was built")
        recent_rows = self.connection.execute(
            "SELECT message_id, direction, raw_text, observed_at FROM messages "
            "WHERE conversation_id=? AND message_id<>? "
            "ORDER BY observed_at DESC, created_at DESC, message_id DESC LIMIT ?",
            (conversation_id, incoming_message_id, MAX_RECENT_MESSAGES),
        ).fetchall()
        recent_rows = list(reversed(recent_rows))
        long_memory_rows = self.connection.execute(
            "SELECT memory_id, kind, memory_key, value_text, confidence FROM memory_items "
            "WHERE player_id=? AND status='active' AND scope='long_term' "
            "ORDER BY confidence DESC, updated_at DESC, memory_id ASC LIMIT ?",
            (player_id, MAX_LONG_TERM_MEMORIES),
        ).fetchall()
        temporary_memory_rows = self.connection.execute(
            "SELECT memory_id, kind, memory_key, value_text, confidence, expires_at FROM memory_items "
            "WHERE player_id=? AND status='active' AND scope='temporary' AND expires_at IS NOT NULL AND expires_at>? "
            "ORDER BY confidence DESC, updated_at DESC, memory_id ASC LIMIT ?",
            (player_id, now, MAX_TEMPORARY_MEMORIES),
        ).fetchall()
        thread_rows = self.connection.execute(
            "SELECT thread_id, title, promise_text, due_at, confidence FROM conversation_threads "
            "WHERE conversation_id=? AND status='open' "
            "ORDER BY due_at IS NULL, due_at, created_at, thread_id LIMIT ?",
            (conversation_id, MAX_OPEN_THREADS),
        ).fetchall()
        promise_rows = self.connection.execute(
            "SELECT promise_id, promise_text, due_at, confidence FROM pending_promises "
            "WHERE conversation_id=? AND status='open' "
            "ORDER BY due_at IS NULL, due_at, created_at, promise_id LIMIT ?",
            (conversation_id, MAX_OPEN_THREADS),
        ).fetchall()
        summary = self.connection.execute(
            "SELECT summary_id, summary_text, covers_through_message_id, confidence FROM conversation_summaries "
            "WHERE conversation_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        def render_context() -> tuple[dict[str, Any], list[tuple[str, str, str]]]:
            aliases: list[tuple[str, str, str]] = [
                ("p0", "player", player_id),
                ("c0", "conversation", conversation_id),
                ("m0", "message", str(incoming["message_id"])),
            ]
            recent_message_aliases: dict[str, str] = {}
            recent_messages: list[dict[str, Any]] = []
            for ordinal, row in enumerate(recent_rows, start=1):
                alias = f"m{ordinal}"
                message_id = str(row["message_id"])
                recent_message_aliases[message_id] = alias
                aliases.append((alias, "message", message_id))
                recent_messages.append(
                    {
                        "message_ref": alias,
                        "direction": str(row["direction"]),
                        "text": str(row["raw_text"]),
                        "observed_at": str(row["observed_at"]),
                    }
                )

            memory_ordinal = 0
            long_term_memory: list[dict[str, Any]] = []
            temporary_memory: list[dict[str, Any]] = []
            for row in long_memory_rows:
                alias = f"mem{memory_ordinal}"
                memory_ordinal += 1
                aliases.append((alias, "memory", str(row["memory_id"])))
                long_term_memory.append(
                    {
                        "memory_ref": alias,
                        "kind": str(row["kind"]),
                        "key": str(row["memory_key"]),
                        "value": str(row["value_text"]),
                        "confidence": float(row["confidence"]),
                    }
                )
            for row in temporary_memory_rows:
                alias = f"mem{memory_ordinal}"
                memory_ordinal += 1
                aliases.append((alias, "memory", str(row["memory_id"])))
                temporary_memory.append(
                    {
                        "memory_ref": alias,
                        "kind": str(row["kind"]),
                        "key": str(row["memory_key"]),
                        "value": str(row["value_text"]),
                        "confidence": float(row["confidence"]),
                        "expires_at": str(row["expires_at"]),
                    }
                )

            open_threads: list[dict[str, Any]] = []
            for ordinal, row in enumerate(thread_rows):
                alias = f"thread{ordinal}"
                aliases.append((alias, "thread", str(row["thread_id"])))
                open_threads.append(
                    {
                        "thread_ref": alias,
                        "title": str(row["title"]),
                        "promise": str(row["promise_text"]),
                        "due_at": row["due_at"],
                        "confidence": float(row["confidence"]),
                    }
                )

            pending_promises: list[dict[str, Any]] = []
            for ordinal, row in enumerate(promise_rows):
                alias = f"promise{ordinal}"
                aliases.append((alias, "promise", str(row["promise_id"])))
                pending_promises.append(
                    {
                        "promise_ref": alias,
                        "text": str(row["promise_text"]),
                        "due_at": row["due_at"],
                        "confidence": float(row["confidence"]),
                    }
                )

            return (
                {
                    "as_of": str(incoming["observed_at"]),
                    "conversation_ref": "c0",
                    "channel": "private",
                    "player": {
                        "player_ref": "p0",
                        "nickname": str(player["primary_nickname"]),
                        "language": player["language"],
                        "alliance": player["alliance_name"],
                        "alliance_role": player["alliance_role"],
                        "relationship_style": player["relationship_style"],
                        "identity_status": str(player["identity_status"]),
                    },
                    "recent_messages": recent_messages,
                    "long_term_memory": long_term_memory,
                    "temporary_memory": temporary_memory,
                    "open_threads": open_threads,
                    "pending_promises": pending_promises,
                    "conversation_summary": (
                        {
                            "text": str(summary["summary_text"]),
                            "confidence": float(summary["confidence"]),
                            "covers_through_message_ref": recent_message_aliases.get(
                                str(summary["covers_through_message_id"])
                            ),
                        }
                        if summary is not None
                        else None
                    ),
                    "incoming_message": {
                        "message_ref": "m0",
                        "direction": "incoming",
                        "text": str(incoming["raw_text"]),
                        "observed_at": str(incoming["observed_at"]),
                    },
                },
                aliases,
            )

        # Compaction runs over only model-safe fields; aliases are regenerated
        # after each removal, keeping m1.. / mem0.. contiguous and deterministic.
        context, aliases = render_context()
        truncated = False
        while len(canonical_json(context)) > MAX_CONTEXT_CHARACTERS:
            if recent_rows:
                recent_rows.pop(0)
            elif temporary_memory_rows:
                temporary_memory_rows.pop()
            elif long_memory_rows:
                long_memory_rows.pop()
            elif thread_rows:
                thread_rows.pop()
            elif promise_rows:
                promise_rows.pop()
            elif summary is not None:
                summary = None
            else:
                # The ingress validator bounds this field.  This pathological
                # fallback does not create a hidden model-only metadata field.
                incoming = dict(incoming)
                incoming["raw_text"] = str(incoming["raw_text"])[:1_000]
                break
            truncated = True
            context, aliases = render_context()

        try:
            from .contracts import validate_conversation_context

            context = validate_conversation_context(context)
        except ImportError:
            # Kept only while upgrading an old pilot installation atomically;
            # the shipped V2 contracts always provide the strict validator.
            pass
        return context, aliases, truncated

    @staticmethod
    def _compact_context(context: dict[str, Any]) -> bool:
        """Drop least-recent/redundant records before exposing a prompt."""

        truncated = False
        while len(canonical_json(context)) > MAX_CONTEXT_CHARACTERS:
            if context["recent_messages"]:
                context["recent_messages"].pop(0)
            elif context["temporary_memory"]:
                context["temporary_memory"].pop()
            elif context["long_term_memory"]:
                context["long_term_memory"].pop()
            elif context["open_threads"]:
                context["open_threads"].pop()
            elif context["pending_promises"]:
                context["pending_promises"].pop()
            elif context["conversation_summary"] is not None:
                context["conversation_summary"] = None
            else:
                # Incoming text is already bounded at ingestion. This fallback
                # only handles a pathological combination of profile fields.
                context["incoming_message"]["text"] = context["incoming_message"]["text"][:1_000]
                break
            truncated = True
        return truncated

    def _store_context_aliases(
        self,
        *,
        snapshot_id: str,
        aliases: list[tuple[str, str, str]],
    ) -> None:
        """Persist the private alias map accompanying one immutable context."""

        for alias, entity_type, entity_id in aliases:
            self.connection.execute(
                "INSERT INTO context_snapshot_aliases(snapshot_id, alias, entity_type, entity_id) "
                "VALUES (?, ?, ?, ?)",
                (snapshot_id, alias, entity_type, entity_id),
            )

    def _task_input(
        self,
        *,
        task_kind: str,
        context: Mapping[str, Any],
        approved_reply: str | None = None,
        allowed_refs: Mapping[str, list[str]] | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """Build a V2 task payload through the versioned contracts module."""

        from . import contracts

        if task_kind == "chat":
            builder = getattr(contracts, "build_task_chat_input", None)
            if builder is not None:
                return (
                    dict(builder(context)),
                    str(contracts.TASK_CHAT),
                    str(contracts.CHAT_PROMPT_VERSION),
                )
            # Compatibility only while an old pilot contracts module is being
            # upgraded.  The V2 implementation always takes the branch above.
            return (
                {
                    "task": contracts.TASK_CHAT,
                    "prompt_version": contracts.CHAT_PROMPT_VERSION,
                    "context_contract_version": contracts.CONTEXT_CONTRACT_VERSION,
                    "context": dict(context),
                },
                str(contracts.TASK_CHAT),
                str(contracts.CHAT_PROMPT_VERSION),
            )
        if task_kind == "memory":
            if not isinstance(approved_reply, str):
                raise RepositoryError("TASK_MEMORY snapshot requires an approved reply")
            if allowed_refs is None:
                raise RepositoryError("TASK_MEMORY snapshot requires allowed alias references")
            builder = getattr(contracts, "build_task_memory_input", None)
            if builder is not None:
                return (
                    dict(builder(context, approved_reply, allowed_refs)),
                    str(contracts.TASK_MEMORY),
                    str(contracts.MEMORY_PROMPT_VERSION),
                )
            return (
                {
                    "task": contracts.TASK_MEMORY,
                    "prompt_version": contracts.MEMORY_PROMPT_VERSION,
                    "context_contract_version": contracts.CONTEXT_CONTRACT_VERSION,
                    "context": dict(context),
                    "approved_reply": approved_reply,
                    "allowed_refs": dict(allowed_refs),
                },
                str(contracts.TASK_MEMORY),
                str(contracts.MEMORY_PROMPT_VERSION),
            )
        raise RepositoryError(f"unsupported task kind {task_kind!r}")

    def _create_task_snapshot(
        self,
        *,
        turn_id: str,
        context_snapshot_id: str,
        task_kind: str,
        context: Mapping[str, Any],
        messages: list[dict[str, str]],
        allowed_refs: Mapping[str, list[str]] | None,
        approved_reply: str | None = None,
    ) -> str:
        """Store one exact task input/prompt, returning the idempotent snapshot."""

        existing = self.connection.execute(
            "SELECT task_snapshot_id, input_json, messages_json, context_snapshot_id FROM task_snapshots "
            "WHERE turn_id=? AND task_kind=?",
            (turn_id, task_kind),
        ).fetchone()
        if existing is not None:
            if str(existing["context_snapshot_id"]) != context_snapshot_id:
                raise TurnStateError("a task snapshot cannot be rebound to a different context snapshot")
            return str(existing["task_snapshot_id"])

        task_input, task_tag, prompt_version = self._task_input(
            task_kind=task_kind,
            context=context,
            approved_reply=approved_reply,
            allowed_refs=allowed_refs,
        )
        input_payload = canonical_json(task_input)
        messages_payload = canonical_json(messages)
        task_snapshot_id = _uuid()
        self.connection.execute(
            "INSERT INTO task_snapshots(task_snapshot_id, turn_id, context_snapshot_id, task_kind, task_tag, "
            "prompt_version, context_contract_version, input_json, input_sha256, messages_json, messages_sha256, "
            "allowed_refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_snapshot_id,
                turn_id,
                context_snapshot_id,
                task_kind,
                task_tag,
                prompt_version,
                CONTEXT_CONTRACT_VERSION,
                input_payload,
                sha256_text(input_payload),
                messages_payload,
                sha256_text(messages_payload),
                canonical_json(dict(allowed_refs)) if allowed_refs is not None else None,
                utc_now(),
            ),
        )
        return task_snapshot_id

    def get_task_snapshot(self, task_snapshot_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM task_snapshots WHERE task_snapshot_id=?", (task_snapshot_id,)
        ).fetchone()
        if row is None:
            raise RepositoryError("task snapshot not found")
        result = dict(row)
        for field in ("input_json", "messages_json", "allowed_refs_json"):
            if result.get(field) is not None:
                result[field] = _json_value(result[field])
        return result

    def chat_task_snapshot_for_turn(self, turn_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT task_snapshot_id FROM task_snapshots WHERE turn_id=? AND task_kind='chat'",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("chat task snapshot not found")
        return self.get_task_snapshot(str(row["task_snapshot_id"]))

    def ensure_memory_task_snapshot(self, turn_id: str) -> dict[str, Any]:
        """Freeze TASK_MEMORY input once, using the original chat snapshot.

        This must be called after human approval.  It never reconstructs the
        context from current mutable memory state.
        """

        turn = self.get_turn(turn_id)
        if turn["approval_status"] != "approved" or not isinstance(turn["approved_reply"], str):
            raise TurnStateError("TASK_MEMORY requires an approved player-ready reply")
        existing = self.connection.execute(
            "SELECT task_snapshot_id FROM task_snapshots WHERE turn_id=? AND task_kind='memory'",
            (turn_id,),
        ).fetchone()
        if existing is not None:
            return self.get_task_snapshot(str(existing["task_snapshot_id"]))
        from .contracts import render_memory_messages

        allowed_refs = self.allowed_refs_for_turn(turn_id)
        messages = render_memory_messages(turn["context_json"], turn["approved_reply"], allowed_refs)
        with self.transaction():
            # Re-read inside the write transaction so concurrent callers get a
            # single immutable prompt rather than competing versions.
            existing = self.connection.execute(
                "SELECT task_snapshot_id FROM task_snapshots WHERE turn_id=? AND task_kind='memory'",
                (turn_id,),
            ).fetchone()
            if existing is not None:
                return self.get_task_snapshot(str(existing["task_snapshot_id"]))
            snapshot_id = self._create_task_snapshot(
                turn_id=turn_id,
                context_snapshot_id=str(turn["snapshot_id"]),
                task_kind="memory",
                context=turn["context_json"],
                messages=messages,
                allowed_refs=allowed_refs,
                approved_reply=str(turn["approved_reply"]),
            )
        return self.get_task_snapshot(snapshot_id)

    def _record_lifecycle_event(
        self,
        *,
        turn_id: str,
        incoming_message_id: str,
        state: str,
        detail: Mapping[str, Any],
        created_at: str | None = None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO turn_lifecycle_events(lifecycle_event_id, turn_id, incoming_message_id, state, detail_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _uuid(),
                turn_id,
                incoming_message_id,
                state,
                canonical_json(dict(detail)),
                created_at or utc_now(),
            ),
        )

    def lifecycle_for_turn(self, turn_id: str) -> dict[str, Any]:
        """Expose unambiguous lifecycle state for observability and tests."""

        row = self.connection.execute(
            "SELECT incoming_message_id, ingested_at, reply_generated_at, approved_at, processed_at, delivered_at "
            "FROM chat_turns WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("turn not found")
        events = self.connection.execute(
            "SELECT state, detail_json, created_at FROM turn_lifecycle_events WHERE turn_id=? ORDER BY rowid",
            (turn_id,),
        ).fetchall()
        return {
            "ingested_at": row["ingested_at"],
            "processed_at": row["processed_at"],
            "reply_generated_at": row["reply_generated_at"],
            "approved_at": row["approved_at"],
            "delivered_at": row["delivered_at"],
            "events": [
                {"state": item["state"], "detail": _json_value(item["detail_json"]), "created_at": item["created_at"]}
                for item in events
            ],
        }

    def cursor_for_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Return the separated V2 cursor markers without inferring state."""

        row = self.connection.execute(
            "SELECT * FROM conversation_cursors WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        if row is None:
            raise RepositoryError("conversation cursor not found")
        return dict(row)

    def cursor_for_turn(self, turn_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT c.* FROM conversation_cursors c JOIN chat_turns t ON t.conversation_id=c.conversation_id "
            "WHERE t.turn_id=?",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("turn cursor not found")
        return dict(row)

    def ingest_revisions_for_message(self, message_id: str) -> list[dict[str, Any]]:
        """Read the immutable capture history for diagnostics/tests only."""

        rows = self.connection.execute(
            "SELECT * FROM message_ingest_revisions WHERE message_id=? ORDER BY rowid",
            (message_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["observation_json"] = _json_value(item["observation_json"])
            result.append(item)
        return result

    def get_turn(self, turn_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT t.*, s.context_json, s.context_sha256, s.context_truncated, "
            "m.raw_text AS incoming_text, m.observed_at AS incoming_observed_at "
            "FROM chat_turns t JOIN context_snapshots s ON s.snapshot_id=t.snapshot_id "
            "JOIN messages m ON m.message_id=t.incoming_message_id WHERE t.turn_id=?",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("turn not found")
        result = dict(row)
        for field in ("context_json", "chat_messages_json", "player_ready_validation_json", "model_provenance_json"):
            if result.get(field) is not None:
                result[field] = _json_value(result[field])
        return result

    def record_generation(
        self,
        *,
        turn_id: str,
        raw_reply: str,
        verdict: Mapping[str, Any],
        model_provenance: Mapping[str, Any],
    ) -> None:
        valid = bool(verdict.get("valid"))
        normalized = verdict.get("normalized_text")
        if not isinstance(normalized, str):
            raise RepositoryError("reply verdict lacks normalized_text")
        with self.transaction():
            turn = self.connection.execute(
                "SELECT approval_status, memory_status, incoming_message_id, conversation_id FROM chat_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            if turn is None:
                raise RepositoryError("turn not found")
            if turn["approval_status"] != "pending":
                raise TurnStateError("cannot generate a reply for a reviewed turn")
            now = utc_now()
            self.connection.execute(
                "UPDATE chat_turns SET generated_reply_raw=?, generated_reply_normalized=?, player_ready_validation_json=?, "
                "generation_status=?, model_provenance_json=?, reply_generated_at=?, updated_at=? WHERE turn_id=?",
                (
                    raw_reply,
                    normalized,
                    canonical_json(dict(verdict)),
                    "generated" if valid else "generation_rejected",
                    canonical_json(dict(model_provenance)),
                    now,
                    now,
                    turn_id,
                ),
            )
            self._record_lifecycle_event(
                turn_id=turn_id,
                incoming_message_id=str(turn["incoming_message_id"]),
                state="reply_generated",
                detail={"player_ready": valid},
                created_at=now,
            )
            self.connection.execute(
                "UPDATE conversation_cursors SET last_reply_generated_message_id=?, updated_at=? WHERE conversation_id=?",
                (turn["incoming_message_id"], now, turn["conversation_id"]),
            )

    def mark_generation_error(self, turn_id: str, reason: str) -> None:
        with self.transaction():
            self.connection.execute(
                "UPDATE chat_turns SET generation_status='generation_error', player_ready_validation_json=?, updated_at=? "
                "WHERE turn_id=? AND approval_status='pending'",
                (canonical_json({"valid": False, "errors": ["model_request_failed"], "detail": reason[:500]}), utc_now(), turn_id),
            )

    def approve_turn(
        self,
        *,
        turn_id: str,
        approved_reply: str,
        verdict: Mapping[str, Any],
        reviewer: str,
    ) -> None:
        if not verdict.get("valid"):
            raise TurnStateError("only a player-ready reply can be approved")
        normalized = verdict.get("normalized_text")
        if not isinstance(normalized, str):
            raise TurnStateError("approval verdict lacks normalized_text")
        with self.transaction():
            row = self.connection.execute(
                "SELECT generated_reply_normalized, approval_status, incoming_message_id, conversation_id FROM chat_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            if row is None:
                raise RepositoryError("turn not found")
            if row["approval_status"] != "pending":
                raise TurnStateError("turn was already reviewed")
            edited_reply = normalized if row["generated_reply_normalized"] != normalized else None
            now = utc_now()
            self.connection.execute(
                "UPDATE chat_turns SET edited_reply=?, approved_reply=?, approval_status='approved', reviewer=?, reviewed_at=?, updated_at=? "
                "WHERE turn_id=?",
                (edited_reply, normalized, reviewer[:80], now, now, turn_id),
            )
            self.connection.execute(
                "UPDATE chat_turns SET approved_at=? WHERE turn_id=?",
                (now, turn_id),
            )
            self._record_lifecycle_event(
                turn_id=turn_id,
                incoming_message_id=str(row["incoming_message_id"]),
                state="approved",
                detail={"reviewer": reviewer[:80]},
                created_at=now,
            )
            self.connection.execute(
                "UPDATE conversation_cursors SET last_approved_message_id=?, updated_at=? WHERE conversation_id=?",
                (row["incoming_message_id"], now, row["conversation_id"]),
            )

    def reject_turn(self, *, turn_id: str, reviewer: str, reason: str) -> None:
        with self.transaction():
            self.connection.execute(
                "UPDATE chat_turns SET approval_status='rejected', reviewer=?, reviewed_at=?, updated_at=?, "
                "player_ready_validation_json=COALESCE(player_ready_validation_json, ?) WHERE turn_id=? AND approval_status='pending'",
                (
                    reviewer[:80],
                    utc_now(),
                    utc_now(),
                    canonical_json({"valid": False, "errors": ["human_rejected"], "detail": reason[:500]}),
                    turn_id,
                ),
            )

    def allowed_refs_for_turn(self, turn_id: str) -> dict[str, list[str]]:
        """Return aliases from the frozen context, safe to put in TASK_MEMORY."""

        task = self.connection.execute(
            "SELECT allowed_refs_json FROM task_snapshots WHERE turn_id=? AND task_kind='memory'",
            (turn_id,),
        ).fetchone()
        if task is not None and task["allowed_refs_json"] is not None:
            saved = _json_value(task["allowed_refs_json"])
            if isinstance(saved, dict):
                return {key: list(value) for key, value in saved.items()}
        turn = self.get_turn(turn_id)
        context = turn["context_json"]
        return {
            "message_refs": [
                str(context["incoming_message"]["message_ref"]),
                *[str(item["message_ref"]) for item in context.get("recent_messages", [])],
            ],
            "memory_refs": [
                *[str(item["memory_ref"]) for item in context.get("long_term_memory", [])],
                *[str(item["memory_ref"]) for item in context.get("temporary_memory", [])],
            ],
            "thread_refs": [str(item["thread_ref"]) for item in context.get("open_threads", [])],
            "promise_refs": [str(item["promise_ref"]) for item in context.get("pending_promises", [])],
        }

    def _alias_entities_for_turn(self, turn_id: str) -> dict[str, tuple[str, str]]:
        rows = self.connection.execute(
            "SELECT a.alias, a.entity_type, a.entity_id FROM context_snapshot_aliases a "
            "JOIN chat_turns t ON t.snapshot_id=a.snapshot_id WHERE t.turn_id=?",
            (turn_id,),
        ).fetchall()
        return {str(row["alias"]): (str(row["entity_type"]), str(row["entity_id"])) for row in rows}

    def allowed_ids_for_turn(self, turn_id: str) -> dict[str, list[str]]:
        """Legacy internal bridge; never use this return value in model input."""

        refs = self.allowed_refs_for_turn(turn_id)
        aliases = self._alias_entities_for_turn(turn_id)

        def ids(refs_for_type: list[str], entity_type: str) -> list[str]:
            result: list[str] = []
            for alias in refs_for_type:
                actual = aliases.get(alias)
                if actual is not None and actual[0] == entity_type:
                    result.append(actual[1])
            return result

        return {
            "message_ids": ids(refs["message_refs"], "message"),
            "memory_ids": ids(refs["memory_refs"], "memory"),
            "thread_ids": ids(refs["thread_refs"], "thread"),
            "promise_ids": ids(refs["promise_refs"], "promise"),
        }

    def record_memory_attempt(
        self,
        *,
        turn_id: str,
        memory_messages: list[dict[str, str]],
        raw_output: str,
        parsed: Mapping[str, Any] | None,
        validation: Mapping[str, Any],
        status: str,
        model_provenance: Mapping[str, Any] | None = None,
        task_snapshot_id: str | None = None,
    ) -> str:
        if status not in {"validated", "rejected"}:
            raise RepositoryError("memory attempt status must be validated or rejected")
        attempt_id = _uuid()
        with self.transaction():
            turn = self.connection.execute(
                "SELECT approval_status, memory_status FROM chat_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            if turn is None:
                raise RepositoryError("turn not found")
            if turn["approval_status"] != "approved":
                raise TurnStateError("memory proposal requires an approved reply")
            if turn["memory_status"] == "applied":
                raise TurnStateError("an applied memory proposal is immutable for this turn")
            if task_snapshot_id is None:
                snapshot = self.connection.execute(
                    "SELECT task_snapshot_id FROM task_snapshots WHERE turn_id=? AND task_kind='memory'",
                    (turn_id,),
                ).fetchone()
                if snapshot is None:
                    raise TurnStateError("TASK_MEMORY requires an immutable task snapshot")
                task_snapshot_id = str(snapshot["task_snapshot_id"])
            snapshot = self.connection.execute(
                "SELECT prompt_version, messages_json FROM task_snapshots "
                "WHERE task_snapshot_id=? AND turn_id=? AND task_kind='memory'",
                (task_snapshot_id, turn_id),
            ).fetchone()
            if snapshot is None:
                raise TurnStateError("memory attempt is not bound to this turn's immutable task snapshot")
            exact_messages = _json_value(snapshot["messages_json"])
            if canonical_json(memory_messages) != canonical_json(exact_messages):
                raise TurnStateError("memory attempt prompt differs from its immutable task snapshot")
            self.connection.execute(
                "INSERT INTO memory_update_attempts(attempt_id, turn_id, task_snapshot_id, memory_prompt_version, memory_messages_json, "
                "raw_output, parsed_json, validation_json, model_provenance_json, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    turn_id,
                    task_snapshot_id,
                    snapshot["prompt_version"],
                    snapshot["messages_json"],
                    raw_output,
                    canonical_json(dict(parsed)) if parsed is not None else None,
                    canonical_json(dict(validation)),
                    canonical_json(dict(model_provenance)) if model_provenance is not None else None,
                    status,
                    utc_now(),
                ),
            )
            self.connection.execute(
                "UPDATE chat_turns SET memory_status=?, updated_at=? WHERE turn_id=?",
                ("validated" if status == "validated" else "rejected", utc_now(), turn_id),
            )
        return attempt_id

    def get_memory_attempt(self, attempt_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM memory_update_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("memory attempt not found")
        result = dict(row)
        for field in ("memory_messages_json", "parsed_json", "validation_json", "model_provenance_json"):
            if result.get(field) is not None:
                result[field] = _json_value(result[field])
        return result

    def apply_validated_memory_update(
        self,
        *,
        attempt_id: str,
        operations: list[dict[str, Any]],
        applied_by: str,
    ) -> None:
        """Apply Python-validated operations with fixed SQL and scope checks."""

        with self.transaction():
            row = self.connection.execute(
                "SELECT a.status, a.turn_id, a.task_snapshot_id, t.player_id, t.conversation_id, t.incoming_message_id "
                "FROM memory_update_attempts a "
                "JOIN chat_turns t ON t.turn_id=a.turn_id WHERE a.attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise RepositoryError("memory attempt not found")
            if row["status"] != "validated":
                raise TurnStateError("only a validated memory proposal can be applied")
            player_id = str(row["player_id"])
            conversation_id = str(row["conversation_id"])
            resolved_operations = self._resolve_memory_operation_aliases(
                turn_id=str(row["turn_id"]),
                task_snapshot_id=str(row["task_snapshot_id"]) if row["task_snapshot_id"] is not None else None,
                operations=operations,
            )
            for operation in resolved_operations:
                self._apply_one_memory_operation(
                    attempt_id=attempt_id,
                    player_id=player_id,
                    conversation_id=conversation_id,
                    operation=operation,
                )
            now = utc_now()
            self.connection.execute(
                "UPDATE memory_update_attempts SET status='applied', applied_by=?, applied_at=? WHERE attempt_id=?",
                (applied_by[:80], now, attempt_id),
            )
            self.connection.execute(
                "UPDATE chat_turns SET memory_status='applied', processed_at=?, updated_at=? WHERE turn_id=?",
                (now, now, str(row["turn_id"])),
            )
            self._record_lifecycle_event(
                turn_id=str(row["turn_id"]),
                incoming_message_id=str(row["incoming_message_id"]),
                state="processed",
                detail={"memory_attempt_id": attempt_id},
                created_at=now,
            )
            self.connection.execute(
                "UPDATE conversation_cursors SET last_processed_message_id=?, updated_at=? WHERE conversation_id=?",
                (row["incoming_message_id"], now, conversation_id),
            )

    def record_delivery(
        self,
        *,
        turn_id: str,
        delivery_key: str,
        delivered_at: str,
    ) -> dict[str, str]:
        """Record a separately confirmed executor success as an outgoing message.

        This is intentionally an acknowledgement API: it never calls the
        executor. A future integration invokes it only after its own delivery
        layer reports success, so unsent drafts never contaminate recent chat
        context or future training examples.
        """

        delivery_key = validate_identity_value(delivery_key)
        canonical_time = parse_utc_timestamp(delivered_at).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.transaction():
            turn = self.connection.execute(
                "SELECT * FROM chat_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            if turn is None:
                raise RepositoryError("turn not found")
            if turn["approval_status"] != "approved" or turn["memory_status"] != "applied":
                raise TurnStateError("delivery acknowledgement requires approved reply and applied memory")
            if not isinstance(turn["approved_reply"], str) or not turn["approved_reply"]:
                raise TurnStateError("delivery acknowledgement lacks an approved reply")
            if turn["delivery_status"] == "delivered" and turn["outgoing_message_id"]:
                return {
                    "turn_id": turn_id,
                    "outgoing_message_id": str(turn["outgoing_message_id"]),
                    "status": "already_delivered",
                }
            duplicate = self.connection.execute(
                "SELECT message_id FROM messages WHERE conversation_id=? AND direction='outgoing' AND source_message_key=?",
                (turn["conversation_id"], delivery_key),
            ).fetchone()
            if duplicate is not None:
                raise TurnStateError("delivery_key already belongs to another outgoing message")
            message_id = _uuid()
            now = utc_now()
            text = str(turn["approved_reply"])
            self.connection.execute(
                "INSERT INTO messages(message_id, conversation_id, player_id, direction, source_message_id, source_message_key, raw_text, normalized_text, content_sha256, observed_at, created_at) "
                "VALUES (?, ?, ?, 'outgoing', NULL, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    turn["conversation_id"],
                    turn["player_id"],
                    delivery_key,
                    text,
                    normalize_text(text),
                    sha256_text(normalize_text(text)),
                    canonical_time,
                    now,
                ),
            )
            self.connection.execute(
                "UPDATE chat_turns SET delivery_status='delivered', outgoing_message_id=?, delivered_at=?, updated_at=? WHERE turn_id=?",
                (message_id, canonical_time, now, turn_id),
            )
            self._record_lifecycle_event(
                turn_id=turn_id,
                incoming_message_id=str(turn["incoming_message_id"]),
                state="delivered",
                detail={"delivery_key": delivery_key, "outgoing_message_id": message_id},
                created_at=canonical_time,
            )
            self.connection.execute(
                "UPDATE conversation_cursors SET last_seen_message_id=?, last_delivered_message_id=?, updated_at=? "
                "WHERE conversation_id=?",
                (message_id, turn["incoming_message_id"], now, turn["conversation_id"]),
            )
        return {"turn_id": turn_id, "outgoing_message_id": message_id, "status": "delivered"}

    def _resolve_memory_operation_aliases(
        self,
        *,
        turn_id: str,
        task_snapshot_id: str | None,
        operations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Resolve V2 aliases only inside Python's fixed SQL mutation path.

        Model output and task snapshots retain aliases.  This narrow translator
        is the only place an alias becomes an internal UUID, and it verifies
        that every reference belongs to the exact immutable context snapshot.
        """

        alias_entities = self._alias_entities_for_turn(turn_id)
        if task_snapshot_id is not None:
            snapshot = self.connection.execute(
                "SELECT allowed_refs_json FROM task_snapshots WHERE task_snapshot_id=? AND turn_id=? AND task_kind='memory'",
                (task_snapshot_id, turn_id),
            ).fetchone()
            if snapshot is None:
                raise TurnStateError("memory operation lacks its task snapshot")
            allowed_refs = _json_value(snapshot["allowed_refs_json"], {})
            if not isinstance(allowed_refs, dict):
                raise TurnStateError("memory task snapshot has malformed allowed references")
        else:
            # Read-only compatibility for pre-V2 attempts.  New model outputs
            # always travel through the branch above.
            allowed_refs = self.allowed_refs_for_turn(turn_id)

        type_to_allowed_key = {
            "message": "message_refs",
            "memory": "memory_refs",
            "thread": "thread_refs",
            "promise": "promise_refs",
        }

        def resolve(reference: Any, entity_type: str, *, legacy_values: list[str] | None = None) -> str:
            if not isinstance(reference, str):
                raise TurnStateError(f"{entity_type} reference is not a string")
            allowed_key = type_to_allowed_key[entity_type]
            permitted = allowed_refs.get(allowed_key, [])
            if reference in permitted:
                entity = alias_entities.get(reference)
                if entity is None or entity[0] != entity_type:
                    raise TurnStateError(f"{entity_type} alias does not belong to this snapshot")
                return entity[1]
            # This is intentionally only a legacy compatibility bridge.  It
            # never adds a raw ID to a V2 prompt or stored task input.
            if legacy_values is not None and reference in legacy_values:
                return reference
            raise TurnStateError(f"{entity_type} reference is outside the immutable allowed_refs scope")

        legacy = self.allowed_ids_for_turn(turn_id)
        resolved: list[dict[str, Any]] = []
        for operation in operations:
            op = operation.get("op")
            if op == "noop":
                resolved.append(dict(operation))
                continue
            evidence_refs = operation.get("evidence_message_refs")
            if evidence_refs is None:
                evidence_refs = operation.get("evidence_message_ids")
            if not isinstance(evidence_refs, list):
                raise TurnStateError("memory operation lacks evidence_message_refs")
            evidence_ids = [
                resolve(item, "message", legacy_values=legacy["message_ids"])
                for item in evidence_refs
            ]
            if op == "upsert_memory":
                resolved.append(
                    {
                        "op": op,
                        "kind": operation["kind"],
                        "key": operation["key"],
                        "value": operation["value"],
                        "scope": operation["scope"],
                        "confidence": operation["confidence"],
                        "expires_at": operation["expires_at"],
                        "evidence_message_ids": evidence_ids,
                    }
                )
                continue
            if op == "expire_memory":
                reference = operation.get("memory_ref", operation.get("memory_id"))
                resolved.append(
                    {
                        "op": op,
                        "memory_id": resolve(reference, "memory", legacy_values=legacy["memory_ids"]),
                        "reason": operation["reason"],
                        "evidence_message_ids": evidence_ids,
                    }
                )
                continue
            if op == "set_profile_field":
                resolved.append(
                    {
                        "op": op,
                        "field": operation["field"],
                        "value": operation["value"],
                        "confidence": operation["confidence"],
                        "evidence_message_ids": evidence_ids,
                    }
                )
                continue
            if op == "open_thread":
                resolved.append(
                    {
                        "op": op,
                        "title": operation["title"],
                        "promise": operation["promise"],
                        "due_at": operation["due_at"],
                        "confidence": operation["confidence"],
                        "evidence_message_ids": evidence_ids,
                    }
                )
                continue
            if op == "close_thread":
                reference = operation.get("thread_ref", operation.get("thread_id"))
                resolved.append(
                    {
                        "op": op,
                        "thread_id": resolve(reference, "thread", legacy_values=legacy["thread_ids"]),
                        "reason": operation["reason"],
                        "evidence_message_ids": evidence_ids,
                    }
                )
                continue
            if op == "open_promise":
                resolved.append(
                    {
                        "op": op,
                        "text": operation["text"],
                        "due_at": operation["due_at"],
                        "confidence": operation["confidence"],
                        "evidence_message_ids": evidence_ids,
                    }
                )
                continue
            if op == "close_promise":
                reference = operation.get("promise_ref", operation.get("promise_id"))
                resolved.append(
                    {
                        "op": op,
                        "promise_id": resolve(reference, "promise", legacy_values=legacy["promise_ids"]),
                        "reason": operation["reason"],
                        "evidence_message_ids": evidence_ids,
                    }
                )
                continue
            # Summary changes are reserved for a future TASK_SUMMARY pipeline.
            raise TurnStateError(f"unsupported V2 memory operation {op!r}")
        return resolved

    def _apply_one_memory_operation(
        self,
        *,
        attempt_id: str,
        player_id: str,
        conversation_id: str,
        operation: Mapping[str, Any],
    ) -> None:
        op = operation["op"]
        now = utc_now()
        if op == "noop":
            return
        if op == "upsert_memory":
            current = self.connection.execute(
                "SELECT * FROM memory_items WHERE player_id=? AND kind=? AND memory_key=? AND status='active' "
                "ORDER BY confidence DESC, updated_at DESC LIMIT 1",
                (player_id, operation["kind"], operation["key"]),
            ).fetchone()
            if current is not None and float(current["confidence"]) > float(operation["confidence"]):
                # A lower-confidence model proposal cannot overwrite a stronger memory.
                return
            if current is not None:
                self.connection.execute(
                    "INSERT INTO memory_revisions(revision_id, memory_id, previous_value_text, previous_confidence, previous_status, reason, attempt_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 'upsert_replaced', ?, ?)",
                    (
                        _uuid(),
                        current["memory_id"],
                        current["value_text"],
                        current["confidence"],
                        current["status"],
                        attempt_id,
                        now,
                    ),
                )
                self.connection.execute(
                    "UPDATE memory_items SET value_text=?, scope=?, confidence=?, expires_at=?, evidence_message_ids_json=?, "
                    "created_from_attempt_id=?, updated_at=? WHERE memory_id=?",
                    (
                        operation["value"],
                        operation["scope"],
                        operation["confidence"],
                        operation["expires_at"],
                        canonical_json(operation["evidence_message_ids"]),
                        attempt_id,
                        now,
                        current["memory_id"],
                    ),
                )
            else:
                self.connection.execute(
                    "INSERT INTO memory_items(memory_id, player_id, kind, memory_key, value_text, scope, confidence, expires_at, status, evidence_message_ids_json, created_from_attempt_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
                    (
                        _uuid(),
                        player_id,
                        operation["kind"],
                        operation["key"],
                        operation["value"],
                        operation["scope"],
                        operation["confidence"],
                        operation["expires_at"],
                        canonical_json(operation["evidence_message_ids"]),
                        attempt_id,
                        now,
                        now,
                    ),
                )
            return
        if op == "expire_memory":
            owned = self.connection.execute(
                "SELECT * FROM memory_items WHERE memory_id=? AND player_id=? AND status='active'",
                (operation["memory_id"], player_id),
            ).fetchone()
            if owned is None:
                raise TurnStateError("memory operation escaped the current player scope")
            self.connection.execute(
                "INSERT INTO memory_revisions(revision_id, memory_id, previous_value_text, previous_confidence, previous_status, reason, attempt_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _uuid(),
                    owned["memory_id"],
                    owned["value_text"],
                    owned["confidence"],
                    owned["status"],
                    operation["reason"],
                    attempt_id,
                    now,
                ),
            )
            self.connection.execute(
                "UPDATE memory_items SET status='retracted', updated_at=? WHERE memory_id=?",
                (now, owned["memory_id"]),
            )
            return
        if op == "set_profile_field":
            column_map = {
                "language": "language",
                "alliance": "alliance_name",
                "alliance_role": "alliance_role",
                "relationship_style": "relationship_style",
            }
            field = str(operation["field"])
            current = self.connection.execute(
                "SELECT confidence FROM profile_field_confidence WHERE player_id=? AND field_name=?",
                (player_id, field),
            ).fetchone()
            if current is not None and float(current["confidence"]) > float(operation["confidence"]):
                return
            column = column_map[field]
            self.connection.execute(
                f"UPDATE players SET {column}=?, updated_at=? WHERE player_id=?",
                (operation["value"], now, player_id),
            )
            self.connection.execute(
                "INSERT INTO profile_field_confidence(player_id, field_name, confidence, evidence_message_ids_json, updated_from_attempt_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(player_id, field_name) DO UPDATE SET "
                "confidence=excluded.confidence, evidence_message_ids_json=excluded.evidence_message_ids_json, "
                "updated_from_attempt_id=excluded.updated_from_attempt_id, updated_at=excluded.updated_at",
                (
                    player_id,
                    field,
                    operation["confidence"],
                    canonical_json(operation["evidence_message_ids"]),
                    attempt_id,
                    now,
                ),
            )
            return
        if op == "open_thread":
            self.connection.execute(
                "INSERT INTO conversation_threads(thread_id, conversation_id, player_id, title, promise_text, due_at, confidence, status, evidence_message_ids_json, created_from_attempt_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                (
                    _uuid(),
                    conversation_id,
                    player_id,
                    operation["title"],
                    operation["promise"],
                    operation["due_at"],
                    operation["confidence"],
                    canonical_json(operation["evidence_message_ids"]),
                    attempt_id,
                    now,
                ),
            )
            return
        if op == "close_thread":
            changed = self.connection.execute(
                "UPDATE conversation_threads SET status='closed', closed_at=?, close_reason=? "
                "WHERE thread_id=? AND conversation_id=? AND player_id=? AND status='open'",
                (now, operation["reason"], operation["thread_id"], conversation_id, player_id),
            ).rowcount
            if changed != 1:
                raise TurnStateError("thread operation escaped the current conversation scope")
            return
        if op == "open_promise":
            self.connection.execute(
                "INSERT INTO pending_promises(promise_id, conversation_id, player_id, promise_text, due_at, confidence, status, evidence_message_ids_json, created_from_attempt_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                (
                    _uuid(),
                    conversation_id,
                    player_id,
                    operation["text"],
                    operation["due_at"],
                    operation["confidence"],
                    canonical_json(operation["evidence_message_ids"]),
                    attempt_id,
                    now,
                ),
            )
            return
        if op == "close_promise":
            changed = self.connection.execute(
                "UPDATE pending_promises SET status='closed', closed_at=?, close_reason=? "
                "WHERE promise_id=? AND conversation_id=? AND player_id=? AND status='open'",
                (now, operation["reason"], operation["promise_id"], conversation_id, player_id),
            ).rowcount
            if changed != 1:
                raise TurnStateError("promise operation escaped the current conversation scope")
            return
        raise RepositoryError(f"unexpected validated operation {op!r}")

    def exportable_turns(self) -> list[dict[str, Any]]:
        """Return only immutable, fully reviewed V2 capture records.

        These records intentionally expose alias-only task snapshots and never
        select real routing columns.  The exporter can therefore write a future
        training candidate without rebuilding a prompt from current state.
        """

        rows = self.connection.execute(
            "SELECT t.*, s.context_json, s.context_sha256, s.context_truncated, "
            "chat_snapshot.input_json AS chat_input_json, chat_snapshot.input_sha256 AS chat_input_sha256, "
            "chat_snapshot.messages_json AS chat_snapshot_messages_json, "
            "chat_snapshot.messages_sha256 AS chat_messages_sha256, "
            "a.attempt_id, a.task_snapshot_id AS memory_task_snapshot_id, a.memory_prompt_version, "
            "a.memory_messages_json, a.parsed_json, a.model_provenance_json AS memory_model_provenance_json, "
            "memory_snapshot.input_json AS memory_input_json, memory_snapshot.input_sha256 AS memory_input_sha256, "
            "memory_snapshot.messages_json AS memory_snapshot_messages_json, "
            "memory_snapshot.messages_sha256 AS memory_messages_sha256, "
            "memory_snapshot.allowed_refs_json AS memory_allowed_refs_json "
            "FROM chat_turns t JOIN context_snapshots s ON s.snapshot_id=t.snapshot_id "
            "JOIN memory_update_attempts a ON a.turn_id=t.turn_id AND a.status='applied' "
            "JOIN task_snapshots chat_snapshot ON chat_snapshot.task_snapshot_id=t.chat_task_snapshot_id "
            "AND chat_snapshot.task_kind='chat' "
            "JOIN task_snapshots memory_snapshot ON memory_snapshot.task_snapshot_id=a.task_snapshot_id "
            "AND memory_snapshot.task_kind='memory' "
            "JOIN players p ON p.player_id=t.player_id "
            "JOIN external_conversation_routes r ON r.conversation_id=t.conversation_id "
            "AND r.channel='private' AND r.verification_state='verified' "
            "WHERE t.approval_status='approved' AND t.memory_status='applied' AND t.delivery_status='delivered' AND s.context_truncated=0 "
            "AND p.identity_status='verified' ORDER BY t.created_at",
        ).fetchall()
        exported: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            for field in (
                "context_json",
                "chat_messages_json",
                "memory_messages_json",
                "parsed_json",
                "model_provenance_json",
                "memory_model_provenance_json",
                "chat_input_json",
                "chat_snapshot_messages_json",
                "memory_input_json",
                "memory_snapshot_messages_json",
                "memory_allowed_refs_json",
            ):
                if record.get(field) is not None:
                    record[field] = _json_value(record[field])
            exported.append(record)
        return exported

    def list_quarantined_observations(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT quarantine_id, reason, observation_json, created_at FROM quarantined_observations ORDER BY created_at"
        ).fetchall()
        return [
            {
                "quarantine_id": str(row["quarantine_id"]),
                "reason": str(row["reason"]),
                "observation": _json_value(row["observation_json"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

