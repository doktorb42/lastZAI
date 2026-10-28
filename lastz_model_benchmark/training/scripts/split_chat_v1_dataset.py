#!/usr/bin/env python3
"""Create player/thread-disjoint train, validation, and test Chat V1 splits.

Rows are assigned by connected components over ``player_group``,
``thread_group``, and an optional explicit ``variant_group``.  Therefore no
message from a player or conversation/thread can appear in both train and a
held-out split, even when several records share it transitively.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chat_v1_contract import ChatV1ContractError, training_row_from_source, validate_source_record
from dataset_common import (
    DATA_ROOT,
    DatasetError,
    add_rejection,
    canonical_json,
    read_jsonl,
    safe_within_training,
    sha256_file,
    utc_now,
    write_json,
    write_jsonl,
)


DEFAULT_INPUT = DATA_ROOT / "processed" / "chat_v1_leak_checked.jsonl"
DEFAULT_TRAIN = DATA_ROOT / "processed" / "chat_v1_train.jsonl"
DEFAULT_VALIDATION = DATA_ROOT / "processed" / "chat_v1_validation.jsonl"
DEFAULT_TEST = DATA_ROOT / "processed" / "chat_v1_test.jsonl"
DEFAULT_REJECTED = DATA_ROOT / "rejected" / "chat_v1_split_rejected.jsonl"
DEFAULT_REPORT = DATA_ROOT / "reports" / "chat_v1_split_report.json"
DEFAULT_QUALITY_GATE = DATA_ROOT / "processed" / "chat_v1_quality_gate.json"
DEFAULT_VALIDATION_REPORT = DATA_ROOT / "reports" / "chat_v1_validation_report.json"
DEFAULT_LEAK_REPORT = DATA_ROOT / "reports" / "chat_v1_benchmark_leakage_report.json"


class SplitError(RuntimeError):
    """A split would not preserve PEva Chat V1 holdout isolation."""


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        first, second = self.find(left), self.find(right)
        if first != second:
            self.parent[second] = first


@dataclass
class Component:
    key: str
    rows: list[dict[str, Any]]
    players: set[str]
    threads: set[str]
    variants: set[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--test", type=Path, default=DEFAULT_TEST)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--quality-gate", type=Path, default=DEFAULT_QUALITY_GATE)
    parser.add_argument("--validation-report", type=Path, default=DEFAULT_VALIDATION_REPORT)
    parser.add_argument("--leak-report", type=Path, default=DEFAULT_LEAK_REPORT)
    parser.add_argument("--validation-ratio", type=float, default=0.10)
    parser.add_argument("--test-ratio", type=float, default=0.10)
    parser.add_argument("--max-ratio-deviation", type=float, default=0.12)
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 0.05 <= args.validation_ratio <= 0.25:
        parser.error("--validation-ratio must be in [0.05, 0.25]")
    if not 0.05 <= args.test_ratio <= 0.25:
        parser.error("--test-ratio must be in [0.05, 0.25]")
    if args.validation_ratio + args.test_ratio >= 0.45:
        parser.error("validation + test ratios must leave a substantial train split")
    if not 0.0 <= args.max_ratio_deviation <= 0.25:
        parser.error("--max-ratio-deviation must be in [0, 0.25]")
    return args


def _read_report(path: Path, expected_stage: str) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    resolved = safe_within_training(path)
    if not resolved.is_file():
        return None, [f"missing upstream {expected_stage} report: {resolved}"]
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"unreadable upstream {expected_stage} report: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"upstream {expected_stage} report is not an object"]
    if payload.get("stage") != expected_stage:
        errors.append(f"upstream report stage is {payload.get('stage')!r}, expected {expected_stage!r}")
    if payload.get("passed") is not True:
        errors.append(f"upstream {expected_stage} report is not passed")
    if payload.get("critical_errors"):
        errors.append(f"upstream {expected_stage} report has critical errors")
    return payload, errors


def _nested(payload: Any, *keys: str) -> Any:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def verify_upstream_chain(input_path: Path, validation_report: Path, leakage_report: Path) -> tuple[dict[str, bool], list[str], dict[str, str]]:
    validation, errors = _read_report(validation_report, "validate_chat_v1_dataset")
    leakage, more = _read_report(leakage_report, "check_chat_v1_leakage")
    errors.extend(more)
    checks = {"validation_passed": False, "leak_scan_passed": False, "hash_lineage_valid": False}
    refs = {"validation_report": str(safe_within_training(validation_report)), "leakage_report": str(safe_within_training(leakage_report))}
    if validation is not None:
        checks["validation_passed"] = validation.get("passed") is True
    if leakage is not None:
        checks["leak_scan_passed"] = bool(_nested(leakage, "checks", "leak_scan_passed"))
    expected_validation_accepted = _nested(validation, "outputs", "accepted", "sha256")
    leakage_input = _nested(leakage, "input", "sha256")
    leakage_accepted = _nested(leakage, "outputs", "accepted", "sha256")
    current = sha256_file(input_path)
    if expected_validation_accepted == leakage_input and leakage_accepted == current:
        checks["hash_lineage_valid"] = True
    else:
        errors.append("validation -> leakage -> split input SHA-256 lineage is broken")
    return checks, list(dict.fromkeys(errors)), refs


def _component_rows(rows: list[dict[str, Any]]) -> list[Component]:
    union = UnionFind()
    for row in rows:
        grouping = row["grouping"]
        player = f"player:{grouping['player_group']}"
        thread = f"thread:{grouping['thread_group']}"
        union.union(player, thread)
        variant = row.get("variant_group")
        if isinstance(variant, str):
            union.union(player, f"variant:{variant}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[union.find(f"player:{row['grouping']['player_group']}")].append(row)
    output: list[Component] = []
    for key, component_rows in grouped.items():
        output.append(
            Component(
                key=key,
                rows=component_rows,
                players={str(row["grouping"]["player_group"]) for row in component_rows},
                threads={str(row["grouping"]["thread_group"]) for row in component_rows},
                variants={str(row["variant_group"]) for row in component_rows if isinstance(row.get("variant_group"), str)},
            )
        )
    return output


def _component_order(components: list[Component], seed: int) -> list[Component]:
    def key(component: Component) -> tuple[str, str]:
        payload = canonical_json(
            {
                "players": sorted(component.players),
                "threads": sorted(component.threads),
                "variants": sorted(component.variants),
                "seed": seed,
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest(), component.key
    return sorted(components, key=key)


def _assign_components(components: list[Component], *, validation_ratio: float, test_ratio: float, seed: int) -> dict[str, list[Component]]:
    if len(components) < 3:
        raise SplitError("at least three player/thread-connected components are required for non-empty train/validation/test splits")
    ordered = _component_order(components, seed)
    total = sum(len(component.rows) for component in ordered)
    targets = {"train": total * (1.0 - validation_ratio - test_ratio), "validation": total * validation_ratio, "test": total * test_ratio}
    assigned: dict[str, list[Component]] = {"train": [], "validation": [], "test": []}
    counts = {"train": 0, "validation": 0, "test": 0}
    # Seed one distinct component into each split.  This is essential: a
    # seemingly good percentage split with an empty test set is not a holdout.
    for split, component in zip(("test", "validation", "train"), ordered[:3]):
        assigned[split].append(component)
        counts[split] += len(component.rows)
    for component in ordered[3:]:
        # Compare the *whole* split after a tentative assignment.  The old
        # local-only distance check kept feeding the small validation/test
        # buckets because each remained closer to its own target than the
        # deliberately large training bucket.  That could leave a seemingly
        # valid corpus with almost nothing to train on.
        def global_cost(candidate: str) -> tuple[float, float, str]:
            projected = {
                name: counts[name] + (len(component.rows) if name == candidate else 0)
                for name in counts
            }
            normalized_error = sum(
                ((projected[name] - targets[name]) / targets[name]) ** 2
                for name in projected
                if targets[name] > 0
            )
            return (
                normalized_error,
                projected[candidate] / targets[candidate] if targets[candidate] else float("inf"),
                candidate,
            )
        split = min(
            counts,
            key=global_cost,
        )
        assigned[split].append(component)
        counts[split] += len(component.rows)
    return assigned


def _overlap(rows_by_split: dict[str, list[dict[str, Any]]], field: str) -> dict[str, list[str]]:
    values: dict[str, set[str]] = {}
    for split, rows in rows_by_split.items():
        if field == "variant_group":
            values[split] = {str(row[field]) for row in rows if isinstance(row.get(field), str)}
        else:
            values[split] = {str(row["grouping"][field]) for row in rows}
    return {
        "train_validation": sorted(values["train"] & values["validation"]),
        "train_test": sorted(values["train"] & values["test"]),
        "validation_test": sorted(values["validation"] & values["test"]),
    }


def split_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        normalized, errors, _details = validate_source_record(row, require_approved=True)
        if normalized is None or errors:
            rejected.append(add_rejection(copy.deepcopy(row), errors or ["invalid source record"], "split_chat_v1_dataset"))
        else:
            eligible.append(normalized)
    components = _component_rows(eligible)
    assignments = _assign_components(components, validation_ratio=args.validation_ratio, test_ratio=args.test_ratio, seed=args.seed) if eligible else {"train": [], "validation": [], "test": []}
    raw_by_split: dict[str, list[dict[str, Any]]] = {
        split: [row for component in components_for_split for row in component.rows]
        for split, components_for_split in assignments.items()
    }
    output = {
        split: [training_row_from_source(row, split=split) for row in source_rows]
        for split, source_rows in raw_by_split.items()
    }
    player_overlap = _overlap(raw_by_split, "player_group")
    thread_overlap = _overlap(raw_by_split, "thread_group")
    variant_overlap = _overlap(raw_by_split, "variant_group")
    total = len(eligible)
    ratios = {split: len(rows_for_split) / total if total else 0.0 for split, rows_for_split in raw_by_split.items()}
    summary = {
        "eligible_rows": len(eligible),
        "rejected_rows": len(rejected),
        "component_count": len(components),
        "component_sizes": sorted(len(component.rows) for component in components),
        "counts": {split: len(rows_for_split) for split, rows_for_split in raw_by_split.items()},
        "ratios": ratios,
        "player_group_overlap": player_overlap,
        "thread_group_overlap": thread_overlap,
        "variant_group_overlap": variant_overlap,
        "category_distribution": {split: dict(sorted(Counter(str(row["category"]) for row in rows_for_split).items())) for split, rows_for_split in raw_by_split.items()},
    }
    return output, rejected, summary


def _processed_errors(rows: list[dict[str, Any]], split: str) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows, 1):
        prefix = f"{split}:{index}"
        if row.get("split") != split:
            errors.append(f"{prefix} has wrong split marker")
        if row.get("task") != "<TASK_CHAT>":
            errors.append(f"{prefix} has wrong task")
        prompt, completion = row.get("prompt"), row.get("completion")
        if not isinstance(prompt, list) or len(prompt) != 2 or [item.get("role") if isinstance(item, dict) else None for item in prompt] != ["system", "user"]:
            errors.append(f"{prefix} prompt must be exactly [system,user]")
        if not isinstance(completion, list) or len(completion) != 1 or completion[0].get("role") != "assistant" or not isinstance(completion[0].get("content"), str):
            errors.append(f"{prefix} completion must be one player-ready assistant text")
        if isinstance(completion, list) and completion and isinstance(completion[0], dict) and completion[0].get("content", "").lstrip().startswith(("{", "[", "```")):
            errors.append(f"{prefix} completion looks structured instead of player-ready text")
    return errors


def _no_overlap(overlap: dict[str, list[str]]) -> bool:
    return not any(overlap.values())


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    upstream_checks, upstream_errors, upstream_refs = verify_upstream_chain(args.input, args.validation_report, args.leak_report)
    try:
        outputs, rejected, summary = split_rows(rows, args)
    except SplitError as exc:
        outputs, rejected, summary = {"train": [], "validation": [], "test": []}, [], {
            "eligible_rows": 0,
            "rejected_rows": 0,
            "component_count": 0,
            "component_sizes": [],
            "counts": {"train": 0, "validation": 0, "test": 0},
            "ratios": {"train": 0.0, "validation": 0.0, "test": 0.0},
            "player_group_overlap": {"train_validation": [], "train_test": [], "validation_test": []},
            "thread_group_overlap": {"train_validation": [], "train_test": [], "validation_test": []},
            "variant_group_overlap": {"train_validation": [], "train_test": [], "validation_test": []},
            "category_distribution": {"train": {}, "validation": {}, "test": {}},
        }
        upstream_errors.append(str(exc))
    processed_errors = sum((_processed_errors(outputs[name], name) for name in ("train", "validation", "test")), [])
    ratio_targets = {"train": 1.0 - args.validation_ratio - args.test_ratio, "validation": args.validation_ratio, "test": args.test_ratio}
    ratio_errors = [
        f"{split} ratio differs from target by more than {args.max_ratio_deviation}"
        for split in ratio_targets
        if abs(summary["ratios"][split] - ratio_targets[split]) > args.max_ratio_deviation
    ]
    critical_errors = list(upstream_errors)
    for split in ("train", "validation", "test"):
        if not outputs[split]:
            critical_errors.append(f"{split} split is empty")
    if not _no_overlap(summary["player_group_overlap"]):
        critical_errors.append("player groups overlap across splits")
    if not _no_overlap(summary["thread_group_overlap"]):
        critical_errors.append("thread groups overlap across splits")
    if not _no_overlap(summary["variant_group_overlap"]):
        critical_errors.append("explicit variant groups overlap across splits")
    critical_errors.extend(ratio_errors)
    critical_errors.extend(processed_errors)
    report = {
        "schema_version": 1,
        "stage": "split_chat_v1_dataset",
        "created_at": utc_now(),
        "input": {"path": str(args.input.expanduser().resolve()), "sha256": sha256_file(args.input), "rows": len(rows)},
        "passed": not critical_errors,
        "critical_errors": list(dict.fromkeys(critical_errors)),
        "upstream_reports": upstream_refs,
        "checks": {
            **upstream_checks,
            "processed_prompt_completion_shape_valid": not processed_errors,
            "nonempty_train_validation_test": all(bool(outputs[split]) for split in ("train", "validation", "test")),
            "player_group_disjoint": _no_overlap(summary["player_group_overlap"]),
            "thread_group_disjoint": _no_overlap(summary["thread_group_overlap"]),
            "variant_group_disjoint": _no_overlap(summary["variant_group_overlap"]),
            "ratio_within_tolerance": not ratio_errors,
        },
        "split": {
            "algorithm": "deterministic_connected_components(player_group,thread_group,variant_group) then seeded ratio-balanced assignment",
            "seed": args.seed,
            "validation_ratio_target": args.validation_ratio,
            "test_ratio_target": args.test_ratio,
            "max_ratio_deviation": args.max_ratio_deviation,
            **summary,
        },
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **report}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["passed"] else 2
    train_path = write_jsonl(args.train, outputs["train"])
    validation_path = write_jsonl(args.validation, outputs["validation"])
    test_path = write_jsonl(args.test, outputs["test"])
    rejected_path = write_jsonl(args.rejected, rejected)
    report["outputs"] = {
        "train": {"path": str(train_path), "sha256": sha256_file(train_path), "rows": len(outputs["train"])},
        "validation": {"path": str(validation_path), "sha256": sha256_file(validation_path), "rows": len(outputs["validation"])},
        "test": {"path": str(test_path), "sha256": sha256_file(test_path), "rows": len(outputs["test"])},
        "rejected": {"path": str(rejected_path), "sha256": sha256_file(rejected_path), "rows": len(rejected)},
    }
    report["sha256"] = {split: report["outputs"][split]["sha256"] for split in ("train", "validation", "test")}
    quality_path = write_json(args.quality_gate, report)
    report_path = write_json(args.report, report)
    print(json.dumps({"report": str(report_path), "quality_gate": str(quality_path), **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DatasetError, SplitError, ChatV1ContractError, ValueError) as exc:
        print(f"chat split failed: {exc}", file=sys.stderr)
        raise SystemExit(2)

