#!/usr/bin/env python3
"""Validate Action V1 JSONL before it can enter a Last Z training split.

This is intentionally a mandatory Python gate: it rejects malformed labels,
raw message generation, coordinate-shaped data, non-production capabilities,
and any completion that is not exactly one canonical selector JSON object.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from dataset_common import (
    DATA_ROOT,
    DatasetError,
    add_rejection,
    canonical_json,
    count_values,
    read_jsonl,
    sha256_file,
    utc_now,
    write_json,
    write_jsonl,
)
from lastz_action_contract import (
    DATASET_VERSION,
    render_messages,
    render_qwen_chatml,
    safe_policy_action,
    validate_production_capabilities,
    validate_selector_output,
)


DEFAULT_INPUT = DATA_ROOT / "generated" / "action_v1_raw.jsonl"
DEFAULT_ACCEPTED = DATA_ROOT / "reviewed" / "action_v1_validated.jsonl"
DEFAULT_REJECTED = DATA_ROOT / "rejected" / "action_v1_validation_rejected.jsonl"
DEFAULT_REPORT = DATA_ROOT / "reports" / "action_v1_validation_report.json"

FORBIDDEN_COORDINATE_KEYS = frozenset(
    {
        "x",
        "y",
        "point",
        "points",
        "coordinate",
        "coordinates",
        "screen_x",
        "screen_y",
        "tap",
        "swipe",
        "drag",
        "adb",
        "device_path",
    }
)
FORBIDDEN_LEAK_LABEL_KEYS = frozenset({"expected", "candidate", "legal", "expected_memory_after"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--accepted", type=Path, default=DEFAULT_ACCEPTED)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-reject", action="store_true")
    return parser.parse_args()


def _walk_keys(value: Any, *, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            key_string = str(key)
            next_path = f"{path}.{key_string}"
            yield next_path, key_string
            yield from _walk_keys(item, path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_keys(item, path=f"{path}[{index}]")


def _coordinate_errors(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for path, key in _walk_keys(record):
        if key.casefold() in FORBIDDEN_COORDINATE_KEYS:
            errors.append(f"coordinate/device-shaped key is forbidden: {path}")
    return errors


def _label_leak_errors(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for path, key in _walk_keys(record):
        if key.casefold() in FORBIDDEN_LEAK_LABEL_KEYS:
            errors.append(f"benchmark-label-shaped key is forbidden in training data: {path}")
    return errors


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return every detectable problem for one record, without mutating it."""

    errors: list[str] = []
    for key in ("id", "suite", "scenario_family", "template_id", "state", "agent_memory", "available_actions", "target", "completion", "messages", "training_text", "metadata"):
        if key not in record:
            errors.append(f"missing required field: {key}")
    if errors:
        return errors
    if record.get("dataset_version") != DATASET_VERSION:
        errors.append(f"dataset_version must be {DATASET_VERSION}")
    if not isinstance(record.get("id"), str) or not record["id"].strip():
        errors.append("id must be a non-empty string")
    if record.get("suite") not in {"production", "adversarial"}:
        errors.append("suite must be production or adversarial")
    if not isinstance(record.get("scenario_family"), str) or not record["scenario_family"].strip():
        errors.append("scenario_family must be a non-empty string")
    if not isinstance(record.get("template_id"), str) or not record["template_id"].strip():
        errors.append("template_id must be a non-empty string")
    if not isinstance(record.get("state"), dict) or not isinstance(record.get("agent_memory"), dict):
        errors.append("state and agent_memory must be objects")
    if not isinstance(record.get("available_actions"), list):
        errors.append("available_actions must be a list")
    if not isinstance(record.get("metadata"), dict):
        errors.append("metadata must be an object")
    errors.extend(_coordinate_errors(record))
    errors.extend(_label_leak_errors(record))

    target = record.get("target")
    valid_target, target_errors = validate_selector_output(target, record)
    if not valid_target:
        errors.extend(f"target: {item}" for item in target_errors)
    policy_target = safe_policy_action(record)
    if target != policy_target:
        errors.append(f"target does not match independent safe policy: {canonical_json(policy_target)}")

    completion = record.get("completion")
    if not isinstance(completion, str):
        errors.append("completion must be a string")
    else:
        try:
            parsed_completion = json.loads(completion)
        except json.JSONDecodeError as exc:
            errors.append(f"completion is not JSON: {exc.msg}")
        else:
            if parsed_completion != target:
                errors.append("completion JSON does not equal target")
            # Canonical equality rejects leading prose, trailing prose, and
            # even gratuitous whitespace, giving the fine-tune a clean target.
            if completion != canonical_json(parsed_completion):
                errors.append("completion must be one canonical JSON object with no prose")

    try:
        expected_messages = render_messages(record, target)
        expected_text = render_qwen_chatml(expected_messages)
    except (TypeError, ValueError) as exc:
        errors.append(f"could not render Qwen conversation: {exc}")
    else:
        if record.get("messages") != expected_messages:
            errors.append("messages do not match the contract renderer")
        if record.get("training_text") != expected_text:
            errors.append("training_text does not match the Qwen ChatML fallback renderer")

    if record.get("suite") == "production":
        errors.extend(f"production capability: {item}" for item in validate_production_capabilities(record))
    return list(dict.fromkeys(errors))


def validate_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    error_counts: Counter[str] = Counter()
    for row in rows:
        errors = validate_record(row)
        row_id = row.get("id")
        if isinstance(row_id, str) and row_id in seen_ids:
            errors.append("duplicate record id")
        elif isinstance(row_id, str):
            seen_ids.add(row_id)
        if errors:
            error_counts.update(errors)
            rejected.append(add_rejection(copy.deepcopy(row), errors, "validate_action_dataset"))
        else:
            accepted.append(row)
    summary = {
        "input_rows": len(rows),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "suite_counts_accepted": count_values(accepted, "suite"),
        "target_counts_accepted": dict(
            sorted(Counter(canonical_json(row.get("target")) for row in accepted).items())
        ),
        "rejection_reasons": dict(sorted(error_counts.items())),
    }
    return accepted, rejected, summary


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    accepted, rejected, summary = validate_rows(rows)
    report = {
        "schema_version": 1,
        "stage": "validate_action_dataset",
        "created_at": utc_now(),
        "input": {"path": str(args.input.expanduser().resolve()), "sha256": sha256_file(args.input)},
        "passed": bool(accepted),
        "critical_errors": [] if accepted else ["no valid action records remain after validation"],
        "checks": {
            "schema_valid": bool(accepted),
            "policy_valid": bool(accepted),
            "production_capabilities_valid": bool(accepted),
            "completion_is_canonical_json": bool(accepted),
            "coordinate_free": bool(accepted),
        },
        **summary,
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **report}, ensure_ascii=False, indent=2, sort_keys=True))
        return 2 if not accepted or (args.fail_on_reject and rejected) else 0
    accepted_path = write_jsonl(args.accepted, accepted)
    rejected_path = write_jsonl(args.rejected, rejected)
    report["outputs"] = {
        "accepted": {"path": str(accepted_path), "sha256": sha256_file(accepted_path), "rows": len(accepted)},
        "rejected": {"path": str(rejected_path), "sha256": sha256_file(rejected_path), "rows": len(rejected)},
    }
    report_path = write_json(args.report, report)
    print(json.dumps({"report": str(report_path), **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if not accepted or (args.fail_on_reject and rejected) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatasetError as exc:
        raise SystemExit(f"dataset validation failed: {exc}")
