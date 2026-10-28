"""Create a privacy-preserving, read-only summary of captured private chat.

This tool may inspect the local SQLite conversation database and the existing
review-only candidate export, but it never copies message text, player IDs,
room IDs, names, or replies into its output.  It also never creates a training
dataset or changes the two input sources.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from charakter.social_reply_policy import PrivateSocialReplyPolicy
from config.runtime_config import DATA_DIR, parse_private_room
from mozg.message_intent import MessageIntentAnalyzer


ROOT = Path(__file__).resolve().parents[1]
CONVERSATION_DB = DATA_DIR / "databases" / "conversation.db"
REVIEW_CANDIDATES = DATA_DIR / "training" / "agency" / "real" / "peva_real_candidates_v0.jsonl"
OUTPUT = DATA_DIR / "training" / "agency" / "review" / "private_message_corpus_summary_v1.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _to_utc(value: Any) -> str | None:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    # Last Z records these fields as Unix milliseconds.
    return datetime.fromtimestamp(numeric / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _field(value: Mapping[str, Any] | Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _analysis_mapping(analysis: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    to_dict = getattr(analysis, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return analysis if isinstance(analysis, Mapping) else {}


def summarize_message_records(
    records: Iterable[Mapping[str, Any]],
    *,
    analyzer: Any,
    policy: PrivateSocialReplyPolicy,
) -> dict[str, Any]:
    """Count message types without retaining any raw message fields."""

    totals: Counter[str] = Counter()
    intents: Counter[str] = Counter()
    preview_reasons: Counter[str] = Counter()
    silent_reasons: Counter[str] = Counter()
    timestamps: list[str] = []

    for record in records:
        totals["all_messages"] += 1
        direction = str(record.get("direction") or "").casefold()
        role = str(record.get("role") or "").casefold()
        room_id = str(record.get("room_id") or "")
        is_private = parse_private_room(room_id) is not None
        if is_private:
            totals["private_messages"] += 1
        else:
            totals["non_private_messages"] += 1
        if direction == "incoming" and role == "user":
            totals["incoming_user_messages"] += 1
            if is_private:
                totals["incoming_private_messages"] += 1
                text = str(record.get("text") or "")
                analysis = analyzer.analyze(
                    {"text": text, "group": "custom", "sender_uid": "review-only"},
                    context={"has_private_context": False},
                )
                payload = _analysis_mapping(analysis)
                intent = str(payload.get("intent") or "UNKNOWN")
                intents[intent] += 1
                decision = policy.evaluate(payload, text)
                destination = preview_reasons if decision.should_generate_preview else silent_reasons
                destination[decision.reason] += 1
        elif direction == "outgoing" and role == "assistant":
            totals["outgoing_assistant_messages"] += 1
        else:
            totals["other_messages"] += 1

        timestamp = _to_utc(record.get("server_time")) or _to_utc(record.get("created_at"))
        if timestamp is not None:
            timestamps.append(timestamp)

    return {
        "counts": dict(sorted(totals.items())),
        "incoming_private_intents": dict(sorted(intents.items())),
        "social_preview_policy": {
            "eligible_preview_count": sum(preview_reasons.values()),
            "kept_silent_count": sum(silent_reasons.values()),
            "eligible_reasons": dict(sorted(preview_reasons.items())),
            "silent_reasons": dict(sorted(silent_reasons.items())),
        },
        "captured_time_utc": {
            "first": min(timestamps) if timestamps else None,
            "last": max(timestamps) if timestamps else None,
        },
    }


def _read_messages_read_only(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Conversation database is missing: {path}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT direction, role, text, room_id, server_time, created_at FROM messages ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def _review_candidate_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Review candidate export is missing: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    invalid_rows = [
        row for row in rows
        if row.get("source") != "real_unreviewed"
        or row.get("synthetic") is not False
        or row.get("eligible_for_positive_training") is not False
    ]
    if invalid_rows:
        raise ValueError("Review candidate export contains a row that is not safely marked review-only.")
    flags: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for row in rows:
        statuses[str(row.get("feedback_status") or "UNREVIEWED")] += 1
        flags.update(str(flag) for flag in row.get("safety_flags", []) if str(flag))
    return {
        "count": len(rows),
        "all_rows_review_only": True,
        "positive_training_eligible_count": 0,
        "feedback_status_counts": dict(sorted(statuses.items())),
        "safety_flag_counts": dict(sorted(flags.items())),
    }


def build_summary() -> dict[str, Any]:
    before = {"conversation_db": _sha256(CONVERSATION_DB), "review_candidates": _sha256(REVIEW_CANDIDATES)}
    message_summary = summarize_message_records(
        _read_messages_read_only(CONVERSATION_DB),
        analyzer=MessageIntentAnalyzer(ai=None),
        policy=PrivateSocialReplyPolicy(),
    )
    candidate_summary = _review_candidate_summary(REVIEW_CANDIDATES)
    after = {"conversation_db": _sha256(CONVERSATION_DB), "review_candidates": _sha256(REVIEW_CANDIDATES)}
    if before != after:
        raise RuntimeError("A source changed while it was being summarized; no summary was written.")
    return {
        "format": "peva_private_message_corpus_summary/v1",
        "sources": {
            "conversation_db": {
                "path": str(CONVERSATION_DB.relative_to(ROOT)).replace("\\", "/"),
                "sha256": before["conversation_db"],
                "access": "read_only",
            },
            "review_candidates": {
                "path": str(REVIEW_CANDIDATES.relative_to(ROOT)).replace("\\", "/"),
                "sha256": before["review_candidates"],
                "access": "read_only",
            },
        },
        "message_corpus": message_summary,
        "review_candidates": candidate_summary,
        "privacy_and_training_guarantees": {
            "raw_message_text_written": False,
            "player_identifiers_written": False,
            "room_identifiers_written": False,
            "automatic_training_started": False,
            "real_messages_used_as_positive_training": False,
            "network_used": False,
        },
    }


def write_summary() -> dict[str, Any]:
    summary = build_summary()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    summary = write_summary()
    print(json.dumps({
        "output": str(OUTPUT.relative_to(ROOT)).replace("\\", "/"),
        "incoming_private_messages": summary["message_corpus"]["counts"].get("incoming_private_messages", 0),
        "preview_eligible": summary["message_corpus"]["social_preview_policy"]["eligible_preview_count"],
        "review_candidates": summary["review_candidates"]["count"],
        "positive_training_eligible": summary["review_candidates"]["positive_training_eligible_count"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
