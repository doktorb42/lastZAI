#!/usr/bin/env python3
"""Audit PEva training data without modifying any training or runtime data.

The audit is deliberately conservative.  It treats pre-existing raw logs as
evidence, not as automatically trainable examples, and reports why a record is
not eligible for a QLoRA release.  It never invokes a model, ADB, game code,
or a database migration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from trening.agency_dataset_policy import positive_real_eligibility

ROOT = Path(__file__).resolve().parents[1]
TRAINING_ROOT = ROOT / "data" / "training"
RAW_ROOT = TRAINING_ROOT / "raw"
PROCESSED_ROOT = TRAINING_ROOT / "processed"
FEEDBACK_DB = ROOT / "data" / "databases" / "feedback.db"
BENCHMARK = ROOT / "benchmarks" / "peva_private_v1.json"
BENCHMARK_SHA = ROOT / "benchmarks" / "peva_private_v1.sha256"
DEFAULT_OUTPUT = TRAINING_ROOT / "audits" / "peva_private_v1_audit.json"

MIN_REPLY = 250
MIN_DECISION = 150
MIN_CATEGORY = 20
REQUIRED_CATEGORIES = ("command", "followup", "memory", "safety")

_TECHNICAL = re.compile(r"(?:^\[|traceback|^\s*\{|exception|error:)", re.I)
_ACTION_CLAIM = re.compile(
    r"\b(?:done|reinforced|sent\s+(?:troops|reinforcements)|"
    r"(?:i(?:'ll| will)\s+)?send(?:ing)?\s+(?:troops|reinforcements)|"
    r"joined(?:\s+the\s+rally)?|teleported|attack(?:ed)?\s+(?:him|them|now)|"
    r"(?:i(?:'ll| will)|i can|i(?:'m| am) going to)\s+"
    r"(?:reinforce|join|teleport|attack|send|come))\b",
    re.I,
)
_BARE_ACCEPTANCE = re.compile(
    r"^\s*(?:sure thing|on it|will do|got it|sounds good|okay|ok|i can(?: do that)?|i(?:'ll| will) do it)\s*[!.]*\s*$",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def canonical(value: Any) -> str:
    return clean(value).casefold()


def is_technical(value: str) -> bool:
    return bool(_TECHNICAL.search(value))


def unsafe_action_claim(reply: str, command_status: Any) -> bool:
    """Match the dataset builder's conservative historical-record policy."""

    return bool(_BARE_ACCEPTANCE.fullmatch(reply)) or (
        str(command_status or "").upper() != "EXECUTED" and bool(_ACTION_CLAIM.search(reply))
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(directory: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    stats = {"files": 0, "rows": 0, "invalid_json_rows": 0}
    if not directory.is_dir():
        return records, stats
    for path in sorted(directory.glob("*.jsonl")):
        stats["files"] += 1
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            stats["rows"] += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                stats["invalid_json_rows"] += 1
                continue
            if isinstance(item, Mapping):
                record = dict(item)
                record["_source_file"] = str(path.relative_to(ROOT))
                records.append(record)
    return records, stats


def read_processed(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not path.is_file():
        return [], {"files": 0, "rows": 0, "invalid_json_rows": 0}
    records: list[dict[str, Any]] = []
    invalid = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(item, Mapping):
            records.append(dict(item))
    return records, {"files": 1, "rows": len(records) + invalid, "invalid_json_rows": invalid}


def assistant_reply(row: Mapping[str, Any]) -> str:
    messages = row.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, Mapping) and str(message.get("role") or "") == "assistant":
            return clean(message.get("content"))
    return ""


def metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    return dict(value) if isinstance(value, Mapping) else {}


def feedback_by_message_key(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read the latest feedback row per key using a read-only SQLite connection."""

    info: dict[str, Any] = {"path": str(path.relative_to(ROOT)), "available": path.is_file(), "rows": 0, "error": None}
    if not path.is_file():
        return {}, info
    try:
        uri = "file:" + path.resolve().as_posix() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT message_key, rating, corrected_reply, original_reply, rewrite_source, guard_reason, timestamp "
            "FROM reply_feedback ORDER BY timestamp DESC"
        ).fetchall()
        connection.close()
    except sqlite3.Error as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        return {}, info
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["message_key"] or "")
        if key and key not in latest:
            latest[key] = dict(row)
    info["rows"] = len(rows)
    info["latest_rows"] = len(latest)
    return latest, info


def benchmark_details(path: Path) -> tuple[set[str], dict[str, Any]]:
    if not path.is_file():
        return set(), {"path": str(path.relative_to(ROOT)), "available": False, "sha256": None, "cases": 0}
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = [dict(item) for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    expected = None
    if BENCHMARK_SHA.is_file():
        expected = BENCHMARK_SHA.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256(path)
    return (
        {canonical(case.get("input")) for case in cases if canonical(case.get("input"))},
        {
            "path": str(path.relative_to(ROOT)),
            "available": True,
            "cases": len(cases),
            "sha256": actual,
            "frozen_sha256": expected,
            "fingerprint_matches": expected == actual if expected else False,
        },
    )


def category_names(item: Mapping[str, Any]) -> set[str]:
    intent = clean(item.get("intent")).upper()
    command = clean(item.get("command_type")).upper()
    status = clean(item.get("command_status")).upper()
    context = item.get("recent_context")
    output: set[str] = set()
    if command and command != "UNKNOWN" or intent.startswith("ACTION"):
        output.add("command")
    if intent == "MEMORY_STATEMENT":
        output.add("memory")
    if intent in {"FOLLOW_UP", "CONFIRMATION", "REJECTION"} or isinstance(context, list) and bool(context):
        output.add("followup")
    if status in {"DETECTED", "WAITING", "BLOCKED"} or bool(item.get("reply_guard_triggered")) or bool(item.get("reply_rewritten")):
        output.add("safety")
    return output


@dataclass(frozen=True)
class Assessment:
    usable: bool
    reasons: tuple[str, ...]
    categories: tuple[str, ...]


def assess(
    *,
    sample_id: str,
    message: str,
    reply: str,
    item: Mapping[str, Any],
    raw: Mapping[str, Any] | None,
    feedback: Mapping[str, Any] | None,
    benchmark_inputs: set[str],
    seen: set[str],
) -> Assessment:
    reasons: list[str] = []
    original = clean((feedback or {}).get("original_reply") or (raw or {}).get("reply") or reply)
    status = (raw or {}).get("command_status", item.get("command_status"))
    rewrite_source = clean((feedback or {}).get("rewrite_source") or item.get("reply_rewrite_source") or (raw or {}).get("reply_rewrite_source"))
    rating = clean((feedback or {}).get("rating") or item.get("quality"))
    room = clean(item.get("room_id") or (raw or {}).get("room_id"))
    if not message or not reply:
        reasons.append("empty")
    if is_technical(message) or is_technical(reply):
        reasons.append("technical")
    if rating.upper() == "BAD":
        reasons.append("bad_feedback")
    if rewrite_source == "deterministic_fallback":
        reasons.append("fallback_rewrite")
    if unsafe_action_claim(original, status):
        reasons.append("unsafe_original")
    policy_record = dict(raw or item)
    policy_record.update({
        "message_key": sample_id,
        "input_message": message,
        "reply": reply,
        "room_id": room,
        "command_status": status,
        "channel": (raw or {}).get("channel", "custom"),
        "player_uid": (raw or {}).get("player_uid", item.get("player_uid", "")),
    })
    policy = positive_real_eligibility(policy_record, feedback)
    if not policy.eligible:
        reasons.extend(f"policy_{reason}" for reason in policy.reasons)
    if canonical(message) in benchmark_inputs or sample_id.startswith("benchmark"):
        reasons.append("benchmark_derived")
    fingerprint = "|".join((canonical(room), canonical(message), canonical(reply)))
    if fingerprint in seen:
        reasons.append("duplicate")
    else:
        seen.add(fingerprint)
    return Assessment(not reasons, tuple(sorted(set(reasons))), tuple(sorted(category_names(item))))


def audit() -> dict[str, Any]:
    benchmark_inputs, benchmark = benchmark_details(BENCHMARK)
    raw_rows, raw_stats = read_jsonl(RAW_ROOT)
    raw_by_key = {str(row.get("message_key") or ""): row for row in raw_rows if row.get("message_key")}
    feedback, feedback_info = feedback_by_message_key(FEEDBACK_DB)
    reply_rows, reply_stats = read_processed(PROCESSED_ROOT / "peva_reply_train.jsonl")
    decision_rows, decision_stats = read_processed(PROCESSED_ROOT / "peva_decision_train.jsonl")

    reply_rejections: Counter[str] = Counter()
    reply_categories: Counter[str] = Counter()
    seen_reply: set[str] = set()
    usable_reply_ids: set[str] = set()
    unsafe_eligible = 0
    for row in reply_rows:
        meta = metadata(row)
        sample_id = clean(meta.get("sample_id"))
        raw = raw_by_key.get(sample_id)
        assessment = assess(
            sample_id=sample_id,
            message=clean(meta.get("input_message")),
            reply=assistant_reply(row),
            item=meta,
            raw=raw,
            feedback=feedback.get(sample_id),
            benchmark_inputs=benchmark_inputs,
            seen=seen_reply,
        )
        if assessment.usable:
            usable_reply_ids.add(sample_id)
            reply_categories.update(assessment.categories)
            if unsafe_action_claim(assistant_reply(row), meta.get("command_status")):
                unsafe_eligible += 1
        else:
            reply_rejections.update(assessment.reasons)

    decision_rejections: Counter[str] = Counter()
    decision_categories: Counter[str] = Counter()
    seen_decision: set[str] = set()
    usable_decision = 0
    for row in decision_rows:
        sample_id = clean(row.get("sample_id"))
        raw = raw_by_key.get(sample_id)
        linked_reply = next((candidate for candidate in reply_rows if clean(metadata(candidate).get("sample_id")) == sample_id), None)
        reply = assistant_reply(linked_reply) if linked_reply is not None else clean((raw or {}).get("reply"))
        assessment = assess(
            sample_id=sample_id,
            message=clean(row.get("input_message")),
            reply=reply,
            item=row,
            raw=raw,
            feedback=feedback.get(sample_id),
            benchmark_inputs=benchmark_inputs,
            seen=seen_decision,
        )
        if assessment.usable and clean(row.get("decision")):
            usable_decision += 1
            decision_categories.update(assessment.categories)
        else:
            decision_rejections.update(assessment.reasons or ("missing_decision",))

    categories = {name: int(max(reply_categories[name], decision_categories[name])) for name in REQUIRED_CATEGORIES}
    gates = {
        "reply_minimum": len(usable_reply_ids) >= MIN_REPLY,
        "decision_minimum": usable_decision >= MIN_DECISION,
        "category_minimum": all(count >= MIN_CATEGORY for count in categories.values()),
        "unsafe_eligible_records_zero": unsafe_eligible == 0,
        "benchmark_fingerprint_frozen": bool(benchmark.get("fingerprint_matches")),
        "benchmark_leakage_excluded": reply_rejections["benchmark_derived"] == 0 and decision_rejections["benchmark_derived"] == 0,
    }
    # Benchmark-like rows are excluded, which is correct, but their presence
    # still blocks a release: a release must be assembled from leak-free data.
    gates["benchmark_leakage_excluded"] = (
        reply_rejections["benchmark_derived"] == 0 and decision_rejections["benchmark_derived"] == 0
    )
    training_gate = all(gates.values())
    return {
        "schema_version": 1,
        "audit": "peva_training_dataset_audit",
        "generated_at_utc": utc_now(),
        "scope": {"training": False, "model_loading": False, "network": False, "data_mutation": False},
        "sources": {
            "raw": raw_stats,
            "processed_reply": reply_stats,
            "processed_decision": decision_stats,
            "feedback": feedback_info,
            "benchmark": benchmark,
        },
        "eligibility": {
            "reply_candidates": len(reply_rows),
            "usable_reply_records": len(usable_reply_ids),
            "decision_candidates": len(decision_rows),
            "usable_decision_records": usable_decision,
            "category_counts": categories,
            "reply_exclusion_counts": dict(sorted(reply_rejections.items())),
            "decision_exclusion_counts": dict(sorted(decision_rejections.items())),
            "unsafe_eligible_records": unsafe_eligible,
        },
        "requirements": {
            "min_usable_reply_records": MIN_REPLY,
            "min_usable_decision_records": MIN_DECISION,
            "min_per_category": MIN_CATEGORY,
            "required_categories": list(REQUIRED_CATEGORIES),
        },
        "gates": gates,
        "training_gate": "passed" if training_gate else "failed",
        "full_qlora_permitted": training_gate,
        "next_safe_step": "create immutable release and run smoke/full QLoRA" if training_gate else "collect and review additional leak-free PEva conversations; do not start full QLoRA",
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit PEva data quality; never trains or mutates source data.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON report path, relative to the repository root by default.")
    parser.add_argument("--require-pass", action="store_true", help="Exit 2 when the QLoRA data gate is not passed.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    report = audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output.relative_to(ROOT)), "training_gate": report["training_gate"], "eligibility": report["eligibility"]}, ensure_ascii=False))
    return 2 if args.require_pass and report["training_gate"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
