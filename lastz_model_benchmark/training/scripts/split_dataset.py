#!/usr/bin/env python3
"""Create a family/template-disjoint Action V1 train/validation split.

The processed outputs use TRL's completion-only record form while retaining
the original message list and metadata for auditability:

``prompt`` is ``[system, user]`` and ``completion`` is ``[assistant]``.
``completion_json`` preserves the pre-split canonical JSON string.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from lastz_action_contract import safe_policy_action, validate_selector_output


DEFAULT_INPUT = DATA_ROOT / "processed" / "action_v1_leak_checked.jsonl"
DEFAULT_TRAIN = DATA_ROOT / "processed" / "action_v1_train.jsonl"
DEFAULT_VALIDATION = DATA_ROOT / "processed" / "action_v1_validation.jsonl"
DEFAULT_REJECTED = DATA_ROOT / "rejected" / "action_v1_split_rejected.jsonl"
DEFAULT_REPORT = DATA_ROOT / "reports" / "action_v1_split_report.json"
DEFAULT_QUALITY_GATE = DATA_ROOT / "processed" / "action_v1_quality_gate.json"
DEFAULT_VALIDATION_REPORT = DATA_ROOT / "reports" / "action_v1_validation_report.json"
DEFAULT_DEDUP_REPORT = DATA_ROOT / "reports" / "action_v1_dedup_report.json"
DEFAULT_LEAK_REPORT = DATA_ROOT / "reports" / "action_v1_benchmark_leakage_report.json"


@dataclass
class Component:
    key: str
    rows: list[dict[str, Any]]
    families: set[str]
    templates: set[str]
    suites: set[str]


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        first, second = self.find(left), self.find(right)
        if first != second:
            self.parent[second] = first


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--quality-gate", type=Path, default=DEFAULT_QUALITY_GATE)
    parser.add_argument("--validation-report", type=Path, default=DEFAULT_VALIDATION_REPORT)
    parser.add_argument("--dedup-report", type=Path, default=DEFAULT_DEDUP_REPORT)
    parser.add_argument("--leak-report", type=Path, default=DEFAULT_LEAK_REPORT)
    parser.add_argument("--validation-ratio", type=float, default=0.10)
    parser.add_argument("--max-ratio-deviation", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 0.05 <= args.validation_ratio <= 0.30:
        parser.error("--validation-ratio must be in [0.05, 0.30]")
    if not 0.0 <= args.max_ratio_deviation <= 0.20:
        parser.error("--max-ratio-deviation must be in [0.0, 0.20]")
    return args


def _nested(mapping: Any, *keys: str) -> Any:
    value = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _read_stage_report(path: Path, stage: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Read an immutable stage report only from the isolated training folder."""

    errors: list[str] = []
    resolved = safe_within_training(path)
    if not resolved.is_file():
        return None, [f"required upstream {stage} report is missing: {resolved}"]
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"required upstream {stage} report is unreadable: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"required upstream {stage} report is not a JSON object"]
    if payload.get("stage") != stage:
        errors.append(f"upstream report {resolved} has stage {payload.get('stage')!r}, expected {stage!r}")
    if payload.get("passed") is not True:
        errors.append(f"upstream {stage} report is not passed")
    critical = payload.get("critical_errors")
    if not isinstance(critical, list) or critical:
        errors.append(f"upstream {stage} report has critical errors")
    return payload, errors


def _verify_upstream_chain(
    *,
    input_path: Path,
    validation_report_path: Path,
    dedup_report_path: Path,
    leak_report_path: Path,
) -> tuple[dict[str, bool], list[str], dict[str, Any]]:
    """Require validated -> deduplicated -> leak-checked hash-linked lineage."""

    validation, errors = _read_stage_report(validation_report_path, "validate_action_dataset")
    dedup, dedup_errors = _read_stage_report(dedup_report_path, "deduplicate_dataset")
    leak, leak_errors = _read_stage_report(leak_report_path, "check_benchmark_leakage")
    errors.extend(dedup_errors)
    errors.extend(leak_errors)
    checks = {
        "schema_valid": False,
        "policy_valid": False,
        "production_capabilities_valid": False,
        "dedup_passed": False,
        "leak_scan_passed": False,
    }
    report_refs = {
        "validation_report": str(safe_within_training(validation_report_path)),
        "dedup_report": str(safe_within_training(dedup_report_path)),
        "leak_report": str(safe_within_training(leak_report_path)),
    }
    if validation is not None:
        validation_checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
        for key in ("schema_valid", "policy_valid", "production_capabilities_valid"):
            checks[key] = validation_checks.get(key) is True
            if not checks[key]:
                errors.append(f"validation report did not assert {key}")
    if dedup is not None:
        dedup_checks = dedup.get("checks") if isinstance(dedup.get("checks"), dict) else {}
        checks["dedup_passed"] = bool(
            dedup_checks.get("exact_duplicate_scan_completed")
            and dedup_checks.get("near_duplicate_scan_completed")
            and dedup_checks.get("accepted_rows_remaining")
        )
        if not checks["dedup_passed"]:
            errors.append("dedup report did not assert completed exact and near-duplicate scans")
    if leak is not None:
        leak_checks = leak.get("checks") if isinstance(leak.get("checks"), dict) else {}
        checks["leak_scan_passed"] = leak_checks.get("leak_scan_passed") is True
        if not checks["leak_scan_passed"]:
            errors.append("leak report did not assert a passed leakage scan")

    validation_output_hash = _nested(validation, "outputs", "accepted", "sha256")
    dedup_input_hash = _nested(dedup, "input", "sha256")
    dedup_output_hash = _nested(dedup, "outputs", "accepted", "sha256")
    leak_input_hash = _nested(leak, "input", "sha256")
    leak_output_hash = _nested(leak, "outputs", "accepted", "sha256")
    current_hash = sha256_file(input_path)
    if not isinstance(validation_output_hash, str) or validation_output_hash != dedup_input_hash:
        errors.append("validation accepted hash does not match dedup input hash")
    if not isinstance(dedup_output_hash, str) or dedup_output_hash != leak_input_hash:
        errors.append("dedup accepted hash does not match leakage-check input hash")
    if not isinstance(leak_output_hash, str) or leak_output_hash != current_hash:
        errors.append("leakage-check accepted hash does not match current split input hash")
    return checks, list(dict.fromkeys(errors)), report_refs


def _row_shape_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    family, template = row.get("scenario_family"), row.get("template_id")
    if not isinstance(family, str) or not family:
        errors.append("missing scenario_family for group-disjoint split")
    if not isinstance(template, str) or not template:
        errors.append("missing template_id for group-disjoint split")
    if row.get("suite") not in {"production", "adversarial"}:
        errors.append("invalid suite")
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) != 3:
        errors.append("messages must be [system, user, assistant]")
    else:
        roles = [message.get("role") if isinstance(message, dict) else None for message in messages]
        if roles != ["system", "user", "assistant"]:
            errors.append("messages must have system, user, assistant roles in order")
    target = row.get("target")
    legal, legal_errors = validate_selector_output(target, row)
    if not legal:
        errors.extend(f"invalid action target: {item}" for item in legal_errors)
    if target != safe_policy_action(row):
        errors.append("target no longer agrees with independent safe policy")
    return errors


def _components(rows: list[dict[str, Any]]) -> list[Component]:
    """Connect family/template IDs so neither can cross split boundaries."""

    union_find = UnionFind()
    for row in rows:
        union_find.union(f"family:{row['scenario_family']}", f"template:{row['template_id']}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        root = union_find.find(f"family:{row['scenario_family']}")
        grouped[root].append(row)
    components: list[Component] = []
    for root, component_rows in grouped.items():
        components.append(
            Component(
                key=root,
                rows=component_rows,
                families={str(row["scenario_family"]) for row in component_rows},
                templates={str(row["template_id"]) for row in component_rows},
                suites={str(row["suite"]) for row in component_rows},
            )
        )
    return components


def _stable_group_order(groups: list[Component], seed: int) -> list[Component]:
    def digest(group: Component) -> str:
        identity = canonical_json(
            {"families": sorted(group.families), "templates": sorted(group.templates), "seed": seed}
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    return sorted(groups, key=lambda group: (digest(group), group.key))


def _choose_validation(groups: list[Component], target_rows: int, seed: int) -> set[str]:
    """Subset-sum selection gives the closest count without splitting a group."""

    ordered = _stable_group_order(groups, seed)
    # rows -> set of component keys. Retain the first seeded ordering for a
    # deterministic tie break and cap nothing: Action V1 has only small group
    # counts, so this remains inexpensive.
    options: dict[int, set[str]] = {0: set()}
    for group in ordered:
        size = len(group.rows)
        additions: dict[int, set[str]] = {}
        for total, selected in options.items():
            candidate = total + size
            if candidate not in options and candidate not in additions:
                additions[candidate] = set(selected) | {group.key}
        options.update(additions)
    nonempty = [(total, selected) for total, selected in options.items() if selected]
    if not nonempty:
        return set()
    total, selected = min(nonempty, key=lambda item: (abs(item[0] - target_rows), item[0] < target_rows, item[0]))
    del total
    return selected


def _choose_validation_stratified(groups: list[Component], target_ratio: float, seed: int) -> set[str]:
    """Keep both production and adversarial evaluation represented when safe."""

    # Family/template components normally have one suite.  If a future corpus
    # intentionally joins suites through a shared template, it must stay a
    # single group, so a global split is the only safe option.
    if any(len(group.suites) != 1 for group in groups):
        return _choose_validation(groups, round(sum(len(group.rows) for group in groups) * target_ratio), seed)
    by_suite: dict[str, list[Component]] = defaultdict(list)
    for group in groups:
        by_suite[next(iter(group.suites))].append(group)
    selected: set[str] = set()
    for offset, suite in enumerate(sorted(by_suite)):
        suite_groups = by_suite[suite]
        suite_count = sum(len(group.rows) for group in suite_groups)
        if suite_count:
            selected.update(_choose_validation(suite_groups, round(suite_count * target_ratio), seed + offset + 1))
    return selected


def _completion_only_record(row: dict[str, Any], split: str) -> dict[str, Any]:
    output = copy.deepcopy(row)
    messages = output["messages"]
    output["completion_json"] = output.get("completion")
    output["prompt"] = copy.deepcopy(messages[:2])
    output["completion"] = [copy.deepcopy(messages[2])]
    output["split"] = split
    return output


def split_rows(
    rows: list[dict[str, Any]], validation_ratio: float, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in rows:
        errors = _row_shape_errors(row)
        if errors:
            rejected.append(add_rejection(copy.deepcopy(row), errors, "split_dataset"))
        else:
            eligible.append(row)
    groups = _components(eligible) if eligible else []
    selected = _choose_validation_stratified(groups, validation_ratio, seed)
    train_raw: list[dict[str, Any]] = []
    validation_raw: list[dict[str, Any]] = []
    for group in groups:
        (validation_raw if group.key in selected else train_raw).extend(group.rows)
    train = [_completion_only_record(row, "train") for row in train_raw]
    validation = [_completion_only_record(row, "validation") for row in validation_raw]
    train_families = {str(row["scenario_family"]) for row in train}
    validation_families = {str(row["scenario_family"]) for row in validation}
    train_templates = {str(row["template_id"]) for row in train}
    validation_templates = {str(row["template_id"]) for row in validation}
    summary = {
        "eligible_rows": len(eligible),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "rejected_rows": len(rejected),
        "validation_ratio_actual": len(validation) / len(eligible) if eligible else 0.0,
        "component_count": len(groups),
        "family_overlap": sorted(train_families & validation_families),
        "template_overlap": sorted(train_templates & validation_templates),
        "train_suite_counts": dict(sorted(Counter(row["suite"] for row in train).items())),
        "validation_suite_counts": dict(sorted(Counter(row["suite"] for row in validation).items())),
    }
    return train, validation, rejected, summary


def _processed_row_errors(rows: list[dict[str, Any]], split: str) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows, 1):
        prefix = f"{split}:{index}"
        if row.get("split") != split:
            errors.append(f"{prefix} has incorrect split marker")
        prompt, completion = row.get("prompt"), row.get("completion")
        if not isinstance(prompt, list) or len(prompt) != 2:
            errors.append(f"{prefix} prompt must contain system and user messages")
        if not isinstance(completion, list) or len(completion) != 1:
            errors.append(f"{prefix} completion must contain one assistant message")
        if isinstance(prompt, list) and [item.get("role") if isinstance(item, dict) else None for item in prompt] != ["system", "user"]:
            errors.append(f"{prefix} prompt roles are not system,user")
        if isinstance(completion, list) and [item.get("role") if isinstance(item, dict) else None for item in completion] != ["assistant"]:
            errors.append(f"{prefix} completion role is not assistant")
        if not isinstance(row.get("completion_json"), str):
            errors.append(f"{prefix} completion_json is missing")
    return errors


def _quality_gate(
    *,
    input_path: Path,
    train_path: Path | None,
    validation_path: Path | None,
    train: list[dict[str, Any]],
    validation: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    summary: dict[str, Any],
    validation_ratio_target: float,
    max_ratio_deviation: float,
    upstream_checks: dict[str, bool],
    upstream_errors: list[str],
    upstream_reports: dict[str, Any],
) -> dict[str, Any]:
    critical_errors: list[str] = list(upstream_errors)
    if not train:
        critical_errors.append("train split is empty")
    if not validation:
        critical_errors.append("validation split is empty")
    if summary["family_overlap"]:
        critical_errors.append("scenario_family values overlap between train and validation")
    if summary["template_overlap"]:
        critical_errors.append("template_id values overlap between train and validation")
    if abs(summary["validation_ratio_actual"] - validation_ratio_target) > max_ratio_deviation:
        critical_errors.append("validation ratio exceeds configured tolerance")
    processed_errors = _processed_row_errors(train, "train") + _processed_row_errors(validation, "validation")
    critical_errors.extend(processed_errors)
    hashes: dict[str, str | None] = {"train": None, "validation": None}
    outputs: dict[str, Any] = {}
    if train_path is not None and validation_path is not None:
        hashes = {"train": sha256_file(train_path), "validation": sha256_file(validation_path)}
        outputs = {
            "train": {"path": str(train_path), "sha256": hashes["train"], "rows": len(train)},
            "validation": {"path": str(validation_path), "sha256": hashes["validation"], "rows": len(validation)},
        }
    return {
        "schema_version": 1,
        "stage": "split_dataset",
        "created_at": utc_now(),
        "passed": not critical_errors,
        "critical_errors": critical_errors,
        "input": {"path": str(input_path.expanduser().resolve()), "sha256": sha256_file(input_path)},
        "outputs": outputs,
        # This top-level mapping is intentionally redundant and stable for
        # train_action_lora.py: exact processed-file hashes keyed by split.
        "sha256": hashes,
        "counts": {
            "input": summary["eligible_rows"] + summary["rejected_rows"],
            "eligible": summary["eligible_rows"],
            "train": len(train),
            "validation": len(validation),
            "rejected": len(rejected),
        },
        "split": {
            "validation_ratio_target": validation_ratio_target,
            "validation_ratio_actual": summary["validation_ratio_actual"],
            "max_ratio_deviation": max_ratio_deviation,
            "group_key": "connected_component(scenario_family,template_id)",
            "component_count": summary["component_count"],
            "family_overlap": summary["family_overlap"],
            "template_overlap": summary["template_overlap"],
        },
        "checks": {
            "schema_valid": upstream_checks["schema_valid"],
            "policy_valid": upstream_checks["policy_valid"],
            "production_capabilities_valid": upstream_checks["production_capabilities_valid"],
            "dedup_passed": upstream_checks["dedup_passed"],
            "leak_scan_passed": upstream_checks["leak_scan_passed"],
            "processed_prompt_completion_shape_valid": not processed_errors,
            "scenario_family_disjoint": not summary["family_overlap"],
            "template_id_disjoint": not summary["template_overlap"],
            "no_critical_errors": not critical_errors,
        },
        "upstream_reports": upstream_reports,
    }


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    upstream_checks, upstream_errors, upstream_reports = _verify_upstream_chain(
        input_path=args.input,
        validation_report_path=args.validation_report,
        dedup_report_path=args.dedup_report,
        leak_report_path=args.leak_report,
    )
    train, validation, rejected, summary = split_rows(rows, args.validation_ratio, args.seed)
    if args.dry_run:
        gate = _quality_gate(
            input_path=args.input,
            train_path=None,
            validation_path=None,
            train=train,
            validation=validation,
            rejected=rejected,
            summary=summary,
            validation_ratio_target=args.validation_ratio,
            max_ratio_deviation=args.max_ratio_deviation,
            upstream_checks=upstream_checks,
            upstream_errors=upstream_errors,
            upstream_reports=upstream_reports,
        )
        print(json.dumps({"dry_run": True, "quality_gate": gate}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if gate["passed"] else 2
    train_path = write_jsonl(args.train, train)
    validation_path = write_jsonl(args.validation, validation)
    rejected_path = write_jsonl(args.rejected, rejected)
    gate = _quality_gate(
        input_path=args.input,
        train_path=train_path,
        validation_path=validation_path,
        train=train,
        validation=validation,
        rejected=rejected,
        summary=summary,
        validation_ratio_target=args.validation_ratio,
        max_ratio_deviation=args.max_ratio_deviation,
        upstream_checks=upstream_checks,
        upstream_errors=upstream_errors,
        upstream_reports=upstream_reports,
    )
    report = {
        "schema_version": 1,
        "stage": "split_dataset",
        "created_at": utc_now(),
        "seed": args.seed,
        "quality_gate_path": str(safe_within_training(args.quality_gate)),
        "rejected": {"path": str(rejected_path), "sha256": sha256_file(rejected_path), "rows": len(rejected)},
        "quality_gate": gate,
    }
    gate_path = write_json(args.quality_gate, gate)
    report_path = write_json(args.report, report)
    print(json.dumps({"report": str(report_path), "quality_gate": str(gate_path), **gate}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if gate["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatasetError as exc:
        raise SystemExit(f"dataset split failed: {exc}")
