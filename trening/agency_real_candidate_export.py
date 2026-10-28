"""Export review-only real private-chat candidates for the Agency pilot."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from trening.agency_dataset_policy import (
    classifications_for,
    is_dummy_test_uid,
    is_unsafe_unconfirmed_action_claim,
    load_cleanup_manifest,
)
from config.runtime_config import DATA_DIR, parse_private_room


ROOT = Path(__file__).resolve().parents[1]
CONVERSATION_DB = DATA_DIR / "databases" / "conversation.db"
FEEDBACK_DB = DATA_DIR / "databases" / "feedback.db"
OUTPUT = DATA_DIR / "training" / "agency" / "real" / "peva_real_candidates_v0.jsonl"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _feedback_by_key() -> dict[str, dict[str, Any]]:
    with sqlite3.connect(FEEDBACK_DB) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM reply_feedback ORDER BY timestamp DESC").fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        output.setdefault(str(item["message_key"]), item)
    return output


def _context(rows: list[sqlite3.Row], index: int) -> list[str]:
    output: list[str] = []
    for row in rows[max(0, index - 6):index]:
        speaker = "PEVA" if row["role"] == "assistant" else "PLAYER"
        output.append(f"{speaker}: {_clean(row['text'])}")
    return output


def _reply_after(rows: list[sqlite3.Row], index: int) -> sqlite3.Row | None:
    for row in rows[index + 1:]:
        if row["role"] == "assistant" and row["direction"] == "outgoing":
            return row
        if row["role"] == "user" and row["direction"] == "incoming":
            return None
    return None


def export_real_candidates() -> list[dict[str, Any]]:
    manifest = load_cleanup_manifest()
    feedback = _feedback_by_key()
    with sqlite3.connect(CONVERSATION_DB) as connection:
        connection.row_factory = sqlite3.Row
        all_rows = connection.execute("SELECT * FROM messages ORDER BY room_id, id").fetchall()
    by_room: dict[str, list[sqlite3.Row]] = {}
    for row in all_rows:
        by_room.setdefault(str(row["room_id"]), []).append(row)
    output: list[dict[str, Any]] = []
    for room_id, rows in sorted(by_room.items()):
        for index, row in enumerate(rows):
            if row["direction"] != "incoming" or row["role"] != "user":
                continue
            player_uid = str(row["player_uid"])
            source_key = str(row["source_message_key"] or "")
            classifications = classifications_for(
                room_id=room_id, player_uid=player_uid, source_message_key=source_key, manifest=manifest,
            )
            if parse_private_room(room_id) is None or is_dummy_test_uid(player_uid):
                continue
            if {"PUBLIC_OR_GROUP", "TEST_COMMAND", "TEST_SAFETY", "TEST_MEMORY", "EXCLUDE_POSITIVE"} & classifications:
                continue
            reply_row = _reply_after(rows, index)
            reply = _clean(reply_row["text"]) if reply_row is not None else None
            feedback_row = feedback.get(source_key)
            rating = _clean((feedback_row or {}).get("rating")) or "UNREVIEWED"
            safety_flags = ["UNREVIEWED_FEEDBACK"] if rating == "UNREVIEWED" else []
            if reply and is_unsafe_unconfirmed_action_claim(reply):
                safety_flags.append("UNSAFE_UNCONFIRMED_ACTION_CLAIM")
            output.append({
                "id": f"real_candidate_{len(output) + 1:03d}",
                "source": "real_unreviewed",
                "synthetic": False,
                "player_uid": player_uid,
                "room_id": room_id,
                "context": _context(rows, index),
                "incoming_text": _clean(row["text"]),
                "peva_reply": reply,
                "feedback_status": rating,
                "safety_flags": safety_flags,
                "cleanup_classification": sorted(classifications) or ["REAL_SOCIAL", "REVIEW_REQUIRED"],
                "eligible_for_positive_training": False,
            })
    return output


def write_export() -> int:
    rows = export_real_candidates()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT.relative_to(ROOT)), "count": len(rows), "positive_eligible": 0}, ensure_ascii=False))
    return len(rows)


def main(argv: Iterable[str] | None = None) -> int:
    del argv
    write_export()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
