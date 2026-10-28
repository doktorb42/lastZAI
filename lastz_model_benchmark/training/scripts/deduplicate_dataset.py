#!/usr/bin/env python3
"""Remove exact and genuinely near-duplicate Action V1 training records."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dataset_common import (
    DATA_ROOT,
    DatasetError,
    add_rejection,
    canonical_json,
    character_ngrams,
    jaccard_similarity,
    read_jsonl,
    sha256_file,
    token_set,
    utc_now,
    write_json,
    write_jsonl,
)
from lastz_action_contract import action_input_payload, render_user_prompt, structural_signature


DEFAULT_INPUT = DATA_ROOT / "reviewed" / "action_v1_validated.jsonl"
DEFAULT_OUTPUT = DATA_ROOT / "processed" / "action_v1_deduplicated.jsonl"
DEFAULT_REJECTED = DATA_ROOT / "rejected" / "action_v1_dedup_rejected.jsonl"
DEFAULT_REPORT = DATA_ROOT / "reports" / "action_v1_dedup_report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--near-threshold",
        type=float,
        default=0.999,
        help="Jaccard threshold for same-structure prompt near duplicates (default: 0.999).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 0.90 <= args.near_threshold <= 1.0:
        parser.error("--near-threshold must be in [0.90, 1.0]")
    return args


def _record_key(record: dict[str, Any]) -> str:
    # ID, metadata, completion formatting, and field insertion order must not
    # defeat an exact semantic prompt duplicate check.
    return canonical_json(action_input_payload(record))


def _bucket_key(record: dict[str, Any]) -> str:
    signature = structural_signature(record)
    return canonical_json(
        {
            "suite": record.get("suite"),
            "target": record.get("target"),
            "structure": signature,
        }
    )


def _near_similarity(left: str, right: str) -> float:
    # Pairwise n-grams make a changed single digit or word visible while
    # avoiding an expensive sequence matcher across unrelated prompt shapes.
    return jaccard_similarity(character_ngrams(left), character_ngrams(right))


def deduplicate_rows(
    rows: list[dict[str, Any]], near_threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    exact_index: dict[str, int] = {}
    buckets: dict[str, list[int]] = defaultdict(list)
    prompt_cache: list[str] = []
    removed_indexes: set[int] = set()
    reasons: Counter[str] = Counter()

    for row in rows:
        exact_key = _record_key(row)
        current_target = canonical_json(row.get("target"))
        if exact_key in exact_index:
            first_index = exact_index[exact_key]
            first = accepted[first_index]
            first_target = canonical_json(first.get("target"))
            if first_target != current_target:
                # Contradictory labels are a critical corpus issue.  Remove
                # both records rather than silently choosing first-wins.
                if first_index not in removed_indexes:
                    removed_indexes.add(first_index)
                    reasons["conflicting labels for identical action input"] += 1
                    rejected.append(
                        add_rejection(
                            copy.deepcopy(first),
                            ["conflicting labels for identical action input"],
                            "deduplicate_dataset",
                        )
                    )
                reasons["conflicting labels for identical action input"] += 1
                rejected.append(
                    add_rejection(
                        copy.deepcopy(row),
                        ["conflicting labels for identical action input"],
                        "deduplicate_dataset",
                    )
                )
            else:
                reasons["exact duplicate action input and label"] += 1
                rejected.append(
                    add_rejection(
                        copy.deepcopy(row),
                        ["exact duplicate action input and label"],
                        "deduplicate_dataset",
                    )
                )
            continue

        prompt = render_user_prompt(row)
        bucket = _bucket_key(row)
        duplicate_of: str | None = None
        for prior_index in buckets[bucket]:
            if prior_index in removed_indexes:
                continue
            score = _near_similarity(prompt, prompt_cache[prior_index])
            if score >= near_threshold:
                duplicate_of = str(accepted[prior_index].get("id", "<unknown>"))
                break
        if duplicate_of is not None:
            reasons["near duplicate action prompt"] += 1
            rejected.append(
                add_rejection(
                    copy.deepcopy(row),
                    [f"near duplicate action prompt of {duplicate_of} (threshold={near_threshold})"],
                    "deduplicate_dataset",
                )
            )
            continue

        exact_index[exact_key] = len(accepted)
        buckets[bucket].append(len(accepted))
        accepted.append(row)
        prompt_cache.append(prompt)

    final_rows = [row for index, row in enumerate(accepted) if index not in removed_indexes]
    return final_rows, rejected, dict(sorted(reasons.items()))


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    accepted, rejected, reasons = deduplicate_rows(rows, args.near_threshold)
    report = {
        "schema_version": 1,
        "stage": "deduplicate_dataset",
        "created_at": utc_now(),
        "input": {"path": str(args.input.expanduser().resolve()), "sha256": sha256_file(args.input), "rows": len(rows)},
        "near_threshold": args.near_threshold,
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "rejection_reasons": reasons,
        "passed": bool(accepted),
        "critical_errors": [] if accepted else ["no rows remain after deduplication"],
        "checks": {
            "exact_duplicate_scan_completed": True,
            "near_duplicate_scan_completed": True,
            "accepted_rows_remaining": bool(accepted),
        },
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **report}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if accepted else 2
    output_path = write_jsonl(args.output, accepted)
    rejected_path = write_jsonl(args.rejected, rejected)
    report["outputs"] = {
        "accepted": {"path": str(output_path), "sha256": sha256_file(output_path), "rows": len(accepted)},
        "rejected": {"path": str(rejected_path), "sha256": sha256_file(rejected_path), "rows": len(rejected)},
    }
    report_path = write_json(args.report, report)
    print(json.dumps({"report": str(report_path), **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if accepted else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatasetError as exc:
        raise SystemExit(f"deduplication failed: {exc}")
