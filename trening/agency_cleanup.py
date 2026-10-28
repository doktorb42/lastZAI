"""Conservative, idempotent PEva data cleanup for the Agency pilot.

The cleanup never deletes historical rows and never changes a database schema.
It only disables one known false runtime memory and applies the pre-existing
``REJECTED`` review status to known development artifacts.  Every decision is
written to the external cleanup manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from trening.agency_dataset_policy import (
    CLEANUP_MANIFEST_PATH,
    PUBLIC_GROUP_ROOMS,
    is_dummy_test_uid,
    is_unsafe_unconfirmed_action_claim,
)
from config.runtime_config import DATA_DIR


ROOT = Path(__file__).resolve().parents[1]
DATABASES = DATA_DIR / "databases"
BACKUP_DIR = DATA_DIR / "backups" / "pre_agency_cleanup_2026-10-20"
DB_NAMES = ("conversation.db", "feedback.db", "humanization.db", "memory_review.db")
OG_UID = "1349515331000772"
FALSE_MEMORY = "my name is Alex"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup_info() -> dict[str, Any]:
    checksum_file = BACKUP_DIR / "SHA256SUMS.json"
    if not checksum_file.is_file():
        raise RuntimeError(f"Required pre-write backup/checksum file is missing: {checksum_file}")
    checksums = json.loads(checksum_file.read_text(encoding="utf-8-sig"))
    if not isinstance(checksums, list):
        raise RuntimeError("Backup checksum file must be a JSON list.")
    listed = {str(item.get("file")): str(item.get("sha256")) for item in checksums if isinstance(item, dict)}
    details: list[dict[str, Any]] = []
    for name in DB_NAMES:
        copy = BACKUP_DIR / name
        if not copy.is_file():
            raise RuntimeError(f"Required backup is missing: {copy}")
        actual = sha256(copy)
        if listed.get(name) != actual:
            raise RuntimeError(f"Backup checksum does not match for: {name}")
        details.append({"file": name, "sha256": actual, "bytes": copy.stat().st_size})
    try:
        backup_path = str(BACKUP_DIR.relative_to(ROOT))
    except ValueError:
        # Tests may use an isolated system temporary copy.  The cleanup still
        # verifies every supplied checksum; only the display path is external.
        backup_path = str(BACKUP_DIR)
    return {"path": backup_path, "checksums": details}


def decision(
    decisions: list[dict[str, Any]],
    *,
    database: str,
    table: str,
    record_identifier: str,
    action: str,
    reason: str,
    original_value: Any,
    resulting_value: Any,
    training_usage: Iterable[str],
    classification: str = "",
    room_id: str = "",
    player_uid: str = "",
    source_message_key: str = "",
) -> None:
    row: dict[str, Any] = {
        "source_database": database,
        "table": table,
        "record_identifier": record_identifier,
        "action": action,
        "reason": reason,
        "original_value": original_value,
        "resulting_value": resulting_value,
        "training_usage": list(training_usage),
    }
    if classification:
        row["classification"] = classification
    if room_id:
        row["room_id"] = room_id
    if player_uid:
        row["player_uid"] = player_uid
    if source_message_key:
        row["source_message_key"] = source_message_key
    decisions.append(row)


def _known_og_message_usage(text: str) -> tuple[str, list[str], str] | None:
    lower = " ".join(text.casefold().split())
    if "peva_v2_stage1_test" in lower:
        return "TEST_SAFETY", ["TEST_SAFETY", "EXCLUDE_POSITIVE"], "DELIBERATE_STAGE_TEST_MARKER"
    if "my name is alex" in lower or "i prefer riders" in lower:
        return "TEST_MEMORY", ["TEST_MEMORY", "EXCLUDE_POSITIVE"], "DELIBERATE_TEST_MEMORY"
    action_words = ("reinforce", "rally", "teleport", "attack", "did you do it")
    if any(word in lower for word in action_words):
        return "TEST_COMMAND", ["TEST_COMMAND", "EXCLUDE_POSITIVE"], "DELIBERATE_ACTION_SAFETY_TEST"
    return None


def inspect_and_apply(*, apply: bool) -> dict[str, Any]:
    backup = backup_info()
    decisions: list[dict[str, Any]] = []
    modifications = Counter()

    conv_path = DATABASES / "conversation.db"
    with closing(sqlite3.connect(conv_path)) as connection:
        connection.row_factory = sqlite3.Row
        for room in sorted(PUBLIC_GROUP_ROOMS):
            rows = connection.execute(
                "SELECT id, player_uid, text, source_message_key FROM messages WHERE room_id = ? ORDER BY id", (room,)
            ).fetchall()
            for row in rows:
                decision(
                    decisions, database="conversation.db", table="messages", record_identifier=str(row["id"]),
                    action="PRESERVE_AND_CLASSIFY", reason="MULTI_SENDER_PUBLIC_OR_GROUP_ROOM",
                    original_value={"room_id": room, "text": row["text"]},
                    resulting_value="PRESERVED; excluded from private training", training_usage=["REAL_PUBLIC", "EXCLUDE_POSITIVE"],
                    classification="PUBLIC_OR_GROUP", room_id=room, player_uid=str(row["player_uid"]),
                    source_message_key=str(row["source_message_key"] or ""),
                )
        og_rows = connection.execute(
            "SELECT id, room_id, player_uid, text, source_message_key FROM messages WHERE player_uid = ? ORDER BY id", (OG_UID,)
        ).fetchall()
        for row in og_rows:
            classified = _known_og_message_usage(str(row["text"]))
            if classified is None:
                classification, usage, reason = (
                    "EXCLUDE_POSITIVE",
                    ["EXCLUDE_POSITIVE", "REVIEW_REQUIRED"],
                    "KNOWN_DEVELOPMENT_TEST_CONVERSATION",
                )
            else:
                classification, usage, reason = classified
            decision(
                decisions, database="conversation.db", table="messages", record_identifier=str(row["id"]),
                action="PRESERVE_AND_CLASSIFY", reason=reason,
                original_value={"text": row["text"]}, resulting_value="PRESERVED; excluded from positive training",
                training_usage=usage, classification=classification, room_id=str(row["room_id"]),
                player_uid=OG_UID, source_message_key=str(row["source_message_key"] or ""),
            )
        false_rows = connection.execute(
            "SELECT id, player_uid, memory_type, content, confidence, active FROM player_memory "
            "WHERE player_uid = ? AND memory_type = 'identity' AND lower(content) = lower(?)", (OG_UID, FALSE_MEMORY)
        ).fetchall()
        for row in false_rows:
            original = {key: row[key] for key in row.keys()}
            changed = bool(row["active"])
            if apply and changed:
                connection.execute("UPDATE player_memory SET active = 0 WHERE id = ?", (row["id"],))
                modifications["conversation.player_memory_deactivated"] += 1
            decision(
                decisions, database="conversation.db", table="player_memory", record_identifier=str(row["id"]),
                action="DEACTIVATE" if changed else "ALREADY_INACTIVE", reason="TEST_DERIVED_FALSE_MEMORY",
                original_value=original, resulting_value={**original, "active": 0}, training_usage=["TEST_MEMORY", "EXCLUDE_POSITIVE"],
                classification="TEST_MEMORY", player_uid=OG_UID,
            )
        if apply:
            connection.commit()

    review_path = DATABASES / "memory_review.db"
    with closing(sqlite3.connect(review_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT candidate_id, player_uid, memory_type, content, source_message_key, status "
            "FROM memory_review_candidates ORDER BY candidate_id"
        ).fetchall()
        for row in rows:
            uid = str(row["player_uid"])
            content = str(row["content"])
            is_og_artifact = uid == OG_UID and content.casefold() in {"my name is alex", "i prefer riders"}
            is_dummy_artifact = is_dummy_test_uid(uid)
            if not (is_og_artifact or is_dummy_artifact):
                continue
            original = {key: row[key] for key in row.keys()}
            changed = str(row["status"]) != "REJECTED"
            if apply and changed:
                connection.execute("UPDATE memory_review_candidates SET status = 'REJECTED' WHERE candidate_id = ?", (row["candidate_id"],))
                modifications["memory_review.rejected"] += 1
            reason = "TEST_DERIVED_MEMORY" if is_og_artifact else "DUMMY_TEST_PLAYER_MEMORY"
            decision(
                decisions, database="memory_review.db", table="memory_review_candidates", record_identifier=str(row["candidate_id"]),
                action="REJECT" if changed else "ALREADY_REJECTED", reason=reason,
                original_value=original, resulting_value={**original, "status": "REJECTED"},
                training_usage=["TEST_MEMORY", "EXCLUDE_POSITIVE"], classification="TEST_MEMORY", player_uid=uid,
                source_message_key=str(row["source_message_key"] or ""),
            )
        if apply:
            connection.commit()

    feedback_path = DATABASES / "feedback.db"
    with closing(sqlite3.connect(feedback_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT feedback_id, message_key, player_uid, model_reply, original_reply, rating, guard_reason FROM reply_feedback ORDER BY feedback_id"
        ).fetchall()
        for row in rows:
            if row["rating"] is not None:
                continue
            reply = str(row["original_reply"] or row["model_reply"] or "")
            unsafe = is_unsafe_unconfirmed_action_claim(reply)
            support_like = any(token in reply.casefold() for token in ("how can i assist", "how can i help", "sure thing"))
            reason = "UNSAFE_UNCONFIRMED_ACTION_CLAIM" if unsafe else "UNREVIEWED_FEEDBACK"
            usage = ["REVIEW_REQUIRED", "EXCLUDE_POSITIVE"]
            classification = "REVIEW_REQUIRED"
            if unsafe:
                usage.insert(0, "TEST_SAFETY")
                classification = "TEST_SAFETY"
            elif support_like:
                reason = "UNREVIEWED_ASSISTANT_LIKE_REPLY"
            decision(
                decisions, database="feedback.db", table="reply_feedback", record_identifier=str(row["feedback_id"]),
                action="PRESERVE_AND_EXCLUDE_FROM_POSITIVE", reason=reason,
                original_value={key: row[key] for key in row.keys()},
                resulting_value="PRESERVED; remains unreviewed", training_usage=usage, classification=classification,
                player_uid=str(row["player_uid"]), source_message_key=str(row["message_key"]),
            )

    human_path = DATABASES / "humanization.db"
    with closing(sqlite3.connect(human_path)) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT player_uid, origin FROM player_style_profiles ORDER BY player_uid"):
            if str(row["origin"]).casefold() == "automatic":
                decision(
                    decisions, database="humanization.db", table="player_style_profiles", record_identifier=str(row["player_uid"]),
                    action="PRESERVE_AS_LEGACY_DEBUG", reason="AUTOMATIC_DEFAULT_PROFILE_IS_NOT_USER_APPROVED",
                    original_value=dict(row), resulting_value="PRESERVED; not positive training data",
                    training_usage=["EXCLUDE_POSITIVE", "REVIEW_REQUIRED"], classification="LEGACY_AUTOMATIC_PROFILE",
                    player_uid=str(row["player_uid"]),
                )
        for row in connection.execute("SELECT review_id, player_uid, status FROM reply_humanization_reviews ORDER BY review_id"):
            if str(row["status"]) == "PENDING":
                decision(
                    decisions, database="humanization.db", table="reply_humanization_reviews", record_identifier=str(row["review_id"]),
                    action="LEAVE_PENDING", reason="HUMAN_REVIEW_REQUIRED",
                    original_value=dict(row), resulting_value=dict(row), training_usage=["REVIEW_REQUIRED", "EXCLUDE_POSITIVE"],
                    classification="REVIEW_REQUIRED", player_uid=str(row["player_uid"]),
                )

    dummy_audit: dict[str, int] = {}
    for db_name, table, uid_column in (
        ("conversation.db", "messages", "player_uid"),
        ("memory_review.db", "memory_review_candidates", "player_uid"),
        ("feedback.db", "reply_feedback", "player_uid"),
    ):
        with closing(sqlite3.connect(DATABASES / db_name)) as connection:
            values = [str(row[0]) for row in connection.execute(f"SELECT DISTINCT {uid_column} FROM {table}")]
        dummy_audit[f"{db_name}:{table}"] = sum(1 for value in values if is_dummy_test_uid(value))

    manifest = {
        "schema_version": 1,
        "cleanup": "peva_conservative_pre_agency_cleanup",
        "generated_at_utc": utc_now(),
        "strategy": "A_conservative_preserve_history",
        "source_data_mutated": bool(apply),
        "backup": backup,
        "decisions": decisions,
        "summary": {
            "decision_count": len(decisions),
            "modifications": dict(sorted(modifications.items())),
            "dummy_uid_audit": dummy_audit,
            "historical_messages_preserved": _message_count(),
        },
    }
    return manifest


def _message_count() -> int:
    with closing(sqlite3.connect(DATABASES / "conversation.db")) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])


def write_manifest(manifest: dict[str, Any]) -> Path:
    CLEANUP_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLEANUP_MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CLEANUP_MANIFEST_PATH


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conservatively classify PEva data; never trains.")
    parser.add_argument("--apply", action="store_true", help="Apply only documented, reversible-status cleanup mutations.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    manifest = inspect_and_apply(apply=args.apply)
    path = write_manifest(manifest)
    print(json.dumps({
        "manifest": str(path.relative_to(ROOT)), "apply": bool(args.apply),
        "decisions": manifest["summary"]["decision_count"], "modifications": manifest["summary"]["modifications"],
        "training_started": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
