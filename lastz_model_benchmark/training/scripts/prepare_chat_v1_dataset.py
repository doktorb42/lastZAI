#!/usr/bin/env python3
"""Import canonical PEva V2 chat captures into the Chat V1 review queue.

This command never generates text, calls a model, interacts with Last Z, or
creates a processed training split.  It turns either immutable V2 review
exports or already-canonical source records into a strictly validated review
queue under ``training/data/raw``.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from chat_v1_contract import (
    REVIEW_EXPORT_RECORD_TYPE,
    SOURCE_RECORD_TYPE,
    ChatV1ContractError,
    source_record_from_review_export,
    validate_source_record,
)
from dataset_common import (
    BUNDLE_ROOT,
    DATA_ROOT,
    DatasetError,
    add_rejection,
    read_jsonl,
    safe_within_training,
    sha256_file,
    utc_now,
    write_json,
    write_jsonl,
)


RAW_DIR = DATA_ROOT / "raw"
REJECTED_DIR = DATA_ROOT / "rejected"
REPORTS_DIR = DATA_ROOT / "reports"
GROUP_MANIFEST_VERSION = "peva-chat-v1-group-manifest-v1"


class PrepareError(RuntimeError):
    """A source/import setup would be unsafe or ambiguous."""


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _input_path(value: Path) -> Path:
    path = value.expanduser().resolve()
    if _within(path, BUNDLE_ROOT / "benchmarks"):
        raise PrepareError("benchmark fixtures are protected and cannot enter the Chat V1 review queue")
    if _within(path, DATA_ROOT / "processed"):
        raise PrepareError("processed Chat/Action data cannot be recycled into the Chat V1 source queue")
    if not path.is_file():
        raise PrepareError(f"input JSONL not found: {path}")
    return path


def _output_path(value: Path, root: Path, label: str) -> Path:
    candidate = value if value.is_absolute() else root.parent.parent / value
    candidate = safe_within_training(candidate)
    if not _within(candidate, root):
        raise PrepareError(f"{label} must stay under {root}")
    if candidate.suffix.casefold() != ".jsonl" and label != "report":
        raise PrepareError(f"{label} must have a .jsonl extension")
    if label == "report" and candidate.suffix.casefold() != ".json":
        raise PrepareError("report must have a .json extension")
    return candidate


def _load_group_manifest(path: Path | None) -> dict[str, dict[str, str]] | None:
    """Load row-specific opaque split groups for a mixed V2 review export."""

    if path is None:
        return None
    source = path.expanduser().resolve()
    if not source.is_file():
        raise PrepareError(f"group manifest not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrepareError(f"group manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "records"}:
        raise PrepareError("group manifest must contain exactly schema_version and records")
    if payload.get("schema_version") != GROUP_MANIFEST_VERSION:
        raise PrepareError(f"group manifest schema_version must be {GROUP_MANIFEST_VERSION}")
    records = payload.get("records")
    if not isinstance(records, dict) or not records:
        raise PrepareError("group manifest records must be a non-empty object keyed by V2 review record_id")
    result: dict[str, dict[str, str]] = {}
    for record_id, assignment in records.items():
        if not isinstance(record_id, str) or not record_id:
            raise PrepareError("group manifest record IDs must be non-empty strings")
        if not isinstance(assignment, dict) or set(assignment) != {"player_group", "thread_group", "category", "length_band"}:
            raise PrepareError(
                f"group manifest record {record_id!r} must contain exactly player_group, thread_group, category, length_band"
            )
        if not all(isinstance(value, str) and value for value in assignment.values()):
            raise PrepareError(f"group manifest record {record_id!r} has an empty/non-text assignment")
        result[record_id] = dict(assignment)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="V2 review export or canonical Chat V1 source JSONL.")
    parser.add_argument("--format", choices=("auto", "v2-review-export", "source-record"), default="auto")
    parser.add_argument("--player-group", help="Opaque pgrp_* alias; required when importing V2 review exports.")
    parser.add_argument("--thread-group", help="Opaque tgrp_* alias; required when importing V2 review exports.")
    parser.add_argument("--category", help="Reviewed category; required when importing V2 review exports.")
    parser.add_argument("--length-band", help="Reviewed reply length band; required when importing a single V2 review export.")
    parser.add_argument(
        "--group-manifest",
        type=Path,
        help="JSON row-to-alias manifest required for a V2 export containing more than one chat review record.",
    )
    parser.add_argument("--reviewer", default="imported_v2_review")
    parser.add_argument("--source-type", default="real_approved", choices=("real_approved", "manually_authored", "synthetic_candidate"))
    parser.add_argument("--output", type=Path, default=RAW_DIR / "chat_v1_source_queue.jsonl")
    parser.add_argument("--rejected", type=Path, default=REJECTED_DIR / "chat_v1_prepare_rejected.jsonl")
    parser.add_argument("--report", type=Path, default=REPORTS_DIR / "chat_v1_prepare_report.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--append", action="store_true", help="Atomically append new distinct source records; default refuses to overwrite a queue.")
    return parser.parse_args()


def _detect_format(row: dict[str, Any]) -> str:
    record_type = row.get("record_type")
    if record_type == REVIEW_EXPORT_RECORD_TYPE:
        return "v2-review-export"
    if record_type == SOURCE_RECORD_TYPE:
        return "source-record"
    return "unknown"


def prepare_rows(args: argparse.Namespace, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    source_formats: Counter[str] = Counter()
    manifest = getattr(args, "group_manifest_records", None)
    v2_ids = {
        str(row["record_id"])
        for row in rows
        if _detect_format(row) == "v2-review-export" and isinstance(row.get("record_id"), str)
    }
    if manifest is not None:
        missing = sorted(v2_ids - set(manifest))
        extra = sorted(set(manifest) - v2_ids)
        if missing or extra:
            details = []
            if missing:
                details.append("missing records=" + ", ".join(missing))
            if extra:
                details.append("unknown records=" + ", ".join(extra))
            raise PrepareError("group manifest must map exactly the V2 records in this import: " + "; ".join(details))
    elif len(v2_ids) > 1:
        raise PrepareError(
            "a V2 review export with multiple chat records requires --group-manifest; "
            "one global player/thread alias would corrupt split isolation"
        )
    for number, row in enumerate(rows, 1):
        detected = _detect_format(row)
        source_formats[detected] += 1
        requested = detected if args.format == "auto" else args.format
        errors: list[str] = []
        try:
            if requested == "v2-review-export":
                assignment = manifest.get(str(row.get("record_id"))) if manifest is not None else None
                if assignment is None:
                    if not args.player_group or not args.thread_group or not args.category or not args.length_band:
                        raise PrepareError(
                            "single V2 review export import requires --player-group, --thread-group, --category, and --length-band"
                        )
                    assignment = {
                        "player_group": args.player_group,
                        "thread_group": args.thread_group,
                        "category": args.category,
                        "length_band": args.length_band,
                    }
                candidate = source_record_from_review_export(
                    row,
                    player_group=assignment["player_group"],
                    thread_group=assignment["thread_group"],
                    category=assignment["category"],
                    length_band=assignment["length_band"],
                    reviewer=args.reviewer,
                    source_type=args.source_type,
                )
            elif requested == "source-record":
                candidate = copy.deepcopy(row)
            else:
                raise PrepareError(f"row {number} is neither a V2 chat review export nor a Chat V1 source record")
            normalized, validation_errors, _details = validate_source_record(candidate, require_approved=False)
            errors.extend(validation_errors)
            if normalized is None:
                errors.append("source record could not be normalized")
            elif not errors:
                accepted.append(normalized)
        except (PrepareError, ChatV1ContractError, ValueError, TypeError) as exc:
            errors.append(str(exc))
        if errors:
            for error in errors:
                reasons[error] += 1
            rejected.append(add_rejection(copy.deepcopy(row), errors, "prepare_chat_v1_dataset"))
    report = {
        "schema_version": 1,
        "stage": "prepare_chat_v1_dataset",
        "created_at": utc_now(),
        "input_rows": len(rows),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "source_formats": dict(sorted(source_formats.items())),
        "source_type_distribution": dict(sorted(Counter(str(row["source_type"]) for row in accepted).items())),
        "approval_state_distribution": dict(sorted(Counter(str(row["approval"]["status"]) for row in accepted).items())),
        "rejection_reasons": dict(sorted(reasons.items())),
        "passed": bool(accepted),
        "critical_errors": [] if accepted else ["no canonical Chat V1 source records survived import"],
        "checks": {
            "canonical_task_chat_v2_validated": bool(accepted),
            "no_model_or_game_generation": True,
            "no_processed_training_data_written": True,
        },
    }
    return accepted, rejected, report


def main() -> int:
    args = parse_args()
    source = _input_path(args.input)
    output = _output_path(args.output, RAW_DIR, "output")
    rejected_path = _output_path(args.rejected, REJECTED_DIR, "rejected")
    report_path = _output_path(args.report, REPORTS_DIR, "report")
    if len({output, rejected_path}) != 2 or source in {output, rejected_path}:
        raise PrepareError("input, output, and rejected paths must be distinct")
    rows = read_jsonl(source)
    args.group_manifest_records = _load_group_manifest(args.group_manifest)
    accepted, rejected, report = prepare_rows(args, rows)
    report["input"] = {"path": str(source), "sha256": sha256_file(source)}
    existing: list[dict[str, Any]] = []
    if output.exists():
        if not args.append:
            if not args.dry_run:
                raise PrepareError(
                    f"refusing to overwrite existing Chat V1 source queue: {output}; use --append after reviewing it"
                )
        else:
            existing = read_jsonl(output)
            invalid_existing = []
            for row in existing:
                normalized, errors, _details = validate_source_record(row, require_approved=False)
                if normalized is None or errors:
                    invalid_existing.append(str(row.get("record_id", "<unknown>")))
            if invalid_existing:
                raise PrepareError("existing source queue contains invalid records: " + ", ".join(invalid_existing[:10]))
            existing_ids = {str(row.get("record_id")) for row in existing}
            incoming_ids = {str(row.get("record_id")) for row in accepted}
            overlaps = sorted(existing_ids & incoming_ids)
            if overlaps:
                raise PrepareError("--append refused duplicate source record IDs: " + ", ".join(overlaps[:10]))
    report["existing_queue_rows"] = len(existing)
    report["append_mode"] = bool(args.append)
    if args.dry_run:
        print(json.dumps({"dry_run": True, **report}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if accepted else 2
    accepted_path = write_jsonl(output, [*existing, *accepted])
    rejected_written = write_jsonl(rejected_path, rejected)
    report["outputs"] = {
        "source_queue": {"path": str(accepted_path), "sha256": sha256_file(accepted_path), "rows": len(existing) + len(accepted)},
        "rejected": {"path": str(rejected_written), "sha256": sha256_file(rejected_written), "rows": len(rejected)},
    }
    final_report = write_json(report_path, report)
    print(json.dumps({"report": str(final_report), **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if accepted else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DatasetError, PrepareError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

