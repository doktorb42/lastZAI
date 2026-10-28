#!/usr/bin/env python3
"""Strictly validate explicitly approved canonical PEva Chat V1 records.

This is the first training-data gate.  It validates the immutable V2 prompt,
the player-ready target, no-runtime-identifier boundary, approval provenance,
and corpus-level duplicate/conflict rules.  It does not train or call a model.
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from chat_v1_contract import (
    ChatV1ContractError,
    LENGTH_BANDS,
    prompt_key,
    reply_word_count,
    target_key,
    validate_source_record,
)
from dataset_common import (
    DATA_ROOT,
    DatasetError,
    add_rejection,
    read_jsonl,
    sha256_file,
    utc_now,
    write_json,
    write_jsonl,
)


DEFAULT_INPUT = DATA_ROOT / "reviewed" / "chat_v1_approved_reviewed.jsonl"
DEFAULT_ACCEPTED = DATA_ROOT / "reviewed" / "chat_v1_validated.jsonl"
DEFAULT_REJECTED = DATA_ROOT / "rejected" / "chat_v1_validation_rejected.jsonl"
DEFAULT_REPORT = DATA_ROOT / "reports" / "chat_v1_validation_report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--accepted", type=Path, default=DEFAULT_ACCEPTED)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-reject", action="store_true", help="Return nonzero if any input row is rejected (always recommended).")
    return parser.parse_args()


def _percentile(values: list[int], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return round(ordered[low] + (ordered[high] - ordered[low]) * fraction, 2)


def _length_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [len(str(row["approved_reply"])) for row in rows if isinstance(row.get("approved_reply"), str)]
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "mean": round(statistics.mean(values), 2) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "bands": {
            "0_80": sum(length <= 80 for length in values),
            "81_180": sum(80 < length <= 180 for length in values),
            "181_400": sum(180 < length <= 400 for length in values),
            "401_950": sum(400 < length <= 950 for length in values),
        },
    }


def _word_length_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [reply_word_count(str(row["approved_reply"])) for row in rows if isinstance(row.get("approved_reply"), str)]
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "mean": round(statistics.mean(values), 2) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "configured_bands": {name: {"min": bounds[0], "max": bounds[1]} for name, bounds in LENGTH_BANDS.items()},
    }


def _append_rejection(rejected: list[dict[str, Any]], row: Mapping[str, Any], reasons: list[str]) -> None:
    rejected.append(add_rejection(copy.deepcopy(dict(row)), reasons, "validate_chat_v1_dataset"))


def _deduplicate_and_resolve_conflicts(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], dict[str, int]]:
    """Reject repeated pairs and fail closed on ungrouped alternate targets."""

    rejected: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    by_prompt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_prompt[prompt_key(row)].append(row)
    kept: list[dict[str, Any]] = []
    variant_group_counts: Counter[str] = Counter()
    for key, group in by_prompt.items():
        if not key:
            for row in group:
                reasons["missing canonical prompt fingerprint"] += 1
                _append_rejection(rejected, row, ["missing canonical prompt fingerprint"])
            continue
        unique_targets: dict[str, dict[str, Any]] = {}
        for row in group:
            reply = row.get("approved_reply")
            if not isinstance(reply, str):
                reasons["approved reply missing during duplicate scan"] += 1
                _append_rejection(rejected, row, ["approved reply missing during duplicate scan"])
                continue
            target = target_key(reply)
            if target in unique_targets:
                reasons["duplicate prompt-target pair"] += 1
                _append_rejection(rejected, row, ["duplicate prompt-target pair"])
            else:
                unique_targets[target] = row
        surviving = list(unique_targets.values())
        if len(surviving) <= 1:
            kept.extend(surviving)
            continue
        variants = {row.get("variant_group") for row in surviving}
        if len(variants) == 1 and None not in variants:
            variant_group = str(next(iter(variants)))
            variant_group_counts[variant_group] += len(surviving)
            kept.extend(surviving)
            continue
        for row in surviving:
            reasons["conflicting approved targets for identical TASK_CHAT input"] += 1
            _append_rejection(
                rejected,
                row,
                [
                    "conflicting approved targets for identical TASK_CHAT input; "
                    "all alternatives require one explicit shared variant_group"
                ],
            )
    return kept, rejected, dict(sorted(reasons.items())), dict(sorted(variant_group_counts.items()))


def validate_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    schema_valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    error_counts: Counter[str] = Counter()
    quality_categories: Counter[str] = Counter()
    seen_ids: set[str] = set()
    approval_states: Counter[str] = Counter()
    for row in rows:
        approval = row.get("approval")
        if isinstance(approval, Mapping):
            approval_states[str(approval.get("status", "<missing>"))] += 1
        normalized, errors, details = validate_source_record(row, require_approved=True)
        for category in details.get("quality_failure_categories", []):
            quality_categories[str(category)] += 1
        record_id = normalized.get("record_id") if isinstance(normalized, Mapping) else row.get("record_id")
        if isinstance(record_id, str) and record_id in seen_ids:
            errors.append("duplicate record_id")
        elif isinstance(record_id, str):
            seen_ids.add(record_id)
        if normalized is None or errors:
            errors = list(dict.fromkeys(errors or ["record could not be normalized"]))
            error_counts.update(errors)
            _append_rejection(rejected, row, errors)
        else:
            schema_valid.append(normalized)
    accepted, duplicates, dedup_reasons, variant_groups = _deduplicate_and_resolve_conflicts(schema_valid)
    rejected.extend(duplicates)
    error_counts.update(dedup_reasons)
    leakage_errors = {
        reason: count
        for reason, count in error_counts.items()
        if any(marker in reason.casefold() for marker in ("identifier", "runtime", "uuid", "path", "storage", "routing"))
    }
    report = {
        "input_rows": len(rows),
        "approved_rows_input": approval_states.get("approved", 0),
        "approval_state_distribution": dict(sorted(approval_states.items())),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "category_distribution": dict(sorted(Counter(str(row["category"]) for row in accepted).items())),
        "source_type_distribution": dict(sorted(Counter(str(row["source_type"]) for row in accepted).items())),
        "player_group_distribution": dict(sorted(Counter(str(row["grouping"]["player_group"]) for row in accepted).items())),
        "thread_group_distribution": dict(sorted(Counter(str(row["grouping"]["thread_group"]) for row in accepted).items())),
        "reply_length_characters": _length_distribution(accepted),
        "reply_length_words": _word_length_distribution(accepted),
        "length_band_distribution": dict(sorted(Counter(str(row["length_band"]) for row in accepted).items())),
        "quality_failure_categories": dict(sorted(quality_categories.items())),
        "generic_assistant_style_failures": {
            key: count
            for key, count in sorted(quality_categories.items())
            if any(marker in key for marker in ("generic_assistant", "support_agent", "assistant_like", "self_identifies", "denies_normal", "formality", "conversation_reset", "unnatural_followup"))
        },
        "leakage_failures": dict(sorted(leakage_errors.items())),
        "duplicate_or_conflict_failures": dict(sorted(dedup_reasons.items())),
        "explicit_valid_variant_groups": variant_groups,
        "rejection_reasons": dict(sorted(error_counts.items())),
    }
    return accepted, rejected, report


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    accepted, rejected, summary = validate_rows(rows)
    report = {
        "schema_version": 1,
        "stage": "validate_chat_v1_dataset",
        "created_at": utc_now(),
        "input": {"path": str(args.input.expanduser().resolve()), "sha256": sha256_file(args.input)},
        "passed": bool(accepted) and not rejected,
        "critical_errors": (
            (["no approved valid Chat V1 records remain"] if not accepted else [])
            + (["one or more records failed strict validation"] if rejected else [])
        ),
        "checks": {
            "exact_task_chat_v2_schema": not any("TASK_CHAT" in reason or "task_snapshot" in reason for reason in summary["rejection_reasons"]),
            "explicit_human_approval_only": all(row["approval"]["status"] == "approved" for row in accepted),
            "player_ready_reply_gate": not any("approved_reply_quality" in reason for reason in summary["rejection_reasons"]),
            "straight_apostrophe_policy": not any("apostrophe" in reason for reason in summary["rejection_reasons"]),
            "runtime_identifier_leak_scan": not bool(summary["leakage_failures"]),
            "duplicate_prompt_target_scan": True,
            "conflicting_target_scan": True,
            "no_rejected_input_rows": not rejected,
        },
        **summary,
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **report}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["passed"] and (not args.fail_on_reject or not rejected) else 2
    accepted_path = write_jsonl(args.accepted, accepted)
    rejected_path = write_jsonl(args.rejected, rejected)
    report["outputs"] = {
        "accepted": {"path": str(accepted_path), "sha256": sha256_file(accepted_path), "rows": len(accepted)},
        "rejected": {"path": str(rejected_path), "sha256": sha256_file(rejected_path), "rows": len(rejected)},
    }
    report_path = write_json(args.report, report)
    print(json.dumps({"report": str(report_path), **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] and (not args.fail_on_reject or not rejected) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DatasetError, ChatV1ContractError, ValueError) as exc:
        print(f"dataset validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)

