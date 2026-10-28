#!/usr/bin/env python3
"""Prove that a Chat V1 corpus is ready before any training command may run.

The checker is deliberately stricter than individual pipeline stages.  It
requires a completed Action V1 protected-production baseline, hash-linked
validation/leakage/split reports, a non-empty held-out test set, a configurable
approved-pilot minimum, and zero quality/leakage failures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from dataset_common import DATA_ROOT, DatasetError, safe_within_training, sha256_file, utc_now, write_json


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
DEFAULT_CONFIG = TRAINING_ROOT / "configs" / "chat_v1.yaml"
DEFAULT_REPORT = DATA_ROOT / "reports" / "chat_v1_readiness_report.json"


class ReadinessError(RuntimeError):
    """A required immutable Chat V1 gate is absent or malformed."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReadinessError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReadinessError(f"{label} is not JSON-compatible YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ReadinessError(f"{label} must be an object")
    return value


def _resolve(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ReadinessError(f"{label} must be a non-empty path string")
    path = Path(value)
    candidate = path if path.is_absolute() else TRAINING_ROOT / path
    return safe_within_training(candidate)


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _bool(value: Any) -> bool:
    return value is True


def _read_required_report(path: Path, stage: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"required {stage} report is missing: {path}")
        return None
    try:
        report = _load_json(path, f"{stage} report")
    except ReadinessError as exc:
        errors.append(str(exc))
        return None
    if report.get("stage") != stage:
        errors.append(f"report {path} has stage {report.get('stage')!r}, expected {stage!r}")
    if report.get("passed") is not True:
        errors.append(f"{stage} report is not passed")
    if report.get("critical_errors"):
        errors.append(f"{stage} report contains critical errors")
    return report


def _action_baseline_ok(report: Mapping[str, Any]) -> bool:
    return bool(
        report.get("status") == "passed"
        and _nested(report, "fine_tuned_strict_production_qualification", "qualifies") is True
        and _nested(report, "fine_tuned_strict_production_qualification", "schema_valid_100") is True
        and _nested(report, "fine_tuned_strict_production_qualification", "legal_100") is True
        and _nested(report, "fine_tuned_strict_production_qualification", "exact_100") is True
    )


def evaluate_readiness(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a serializable readiness result without performing training."""

    errors: list[str] = []
    dataset = config.get("dataset") if isinstance(config.get("dataset"), Mapping) else {}
    gates = config.get("readiness") if isinstance(config.get("readiness"), Mapping) else {}
    action_gate = config.get("action_v1_baseline") if isinstance(config.get("action_v1_baseline"), Mapping) else {}
    try:
        validation_path = _resolve(dataset.get("validation_report_path"), "dataset.validation_report_path")
        leakage_path = _resolve(dataset.get("leakage_report_path"), "dataset.leakage_report_path")
        split_path = _resolve(dataset.get("quality_gate_path"), "dataset.quality_gate_path")
        train_path = _resolve(dataset.get("train_path"), "dataset.train_path")
        validation_data_path = _resolve(dataset.get("validation_path"), "dataset.validation_path")
        test_path = _resolve(dataset.get("test_path"), "dataset.test_path")
        action_report_path = _resolve(action_gate.get("comparison_report_path"), "action_v1_baseline.comparison_report_path")
    except ReadinessError as exc:
        return {"ready": False, "errors": [str(exc)], "checks": {}, "reports": {}, "counts": {}}

    validation = _read_required_report(validation_path, "validate_chat_v1_dataset", errors)
    leakage = _read_required_report(leakage_path, "check_chat_v1_leakage", errors)
    split = _read_required_report(split_path, "split_chat_v1_dataset", errors)
    action_report: dict[str, Any] | None = None
    if action_report_path.is_file():
        try:
            action_report = _load_json(action_report_path, "Action V1 comparison report")
        except ReadinessError as exc:
            errors.append(str(exc))
    else:
        errors.append(f"Action V1 protected-production comparison report missing: {action_report_path}")
    action_ok = _action_baseline_ok(action_report) if action_report is not None else False
    if not action_ok:
        errors.append("Action V1 frozen baseline has not demonstrated strict production schema/legal/exact=100% qualification")

    checks: dict[str, bool] = {
        "action_v1_frozen_qualification": action_ok,
        "validation_report_passed": bool(validation and validation.get("passed") is True),
        "leakage_report_passed": bool(leakage and leakage.get("passed") is True),
        "split_report_passed": bool(split and split.get("passed") is True),
        "hash_lineage_valid": False,
        "nonempty_train_validation_test": False,
        "approved_pilot_minimum": False,
        "quality_failure_counters_zero": False,
        "leakage_failure_counter_zero": False,
        "required_categories_covered": False,
        "required_length_bands_covered": False,
    }
    current_hashes: dict[str, str | None] = {"train": None, "validation": None, "test": None}
    for split_name, path in (("train", train_path), ("validation", validation_data_path), ("test", test_path)):
        if not path.is_file():
            errors.append(f"required {split_name} split is missing: {path}")
        else:
            current_hashes[split_name] = sha256_file(path)

    validation_hash = _nested(validation, "outputs", "accepted", "sha256")
    leakage_input_hash = _nested(leakage, "input", "sha256")
    leakage_output_hash = _nested(leakage, "outputs", "accepted", "sha256")
    split_input_hash = _nested(split, "input", "sha256")
    split_hashes = split.get("sha256") if isinstance(split, Mapping) and isinstance(split.get("sha256"), Mapping) else {}
    if (
        isinstance(validation_hash, str)
        and validation_hash == leakage_input_hash
        and leakage_output_hash == split_input_hash
        and all(isinstance(split_hashes.get(name), str) and split_hashes.get(name) == current_hashes[name] for name in current_hashes)
    ):
        checks["hash_lineage_valid"] = True
    else:
        errors.append("validated -> leakage-checked -> split SHA-256 lineage is not intact")

    split_counts = _nested(split, "split", "counts")
    if isinstance(split_counts, Mapping) and all(int(split_counts.get(name, 0)) > 0 for name in ("train", "validation", "test")):
        checks["nonempty_train_validation_test"] = True
    else:
        errors.append("train, validation, and protected test splits must all be non-empty")

    min_approved = gates.get("minimum_approved_valid_rows", 300)
    try:
        min_approved = int(min_approved)
    except (TypeError, ValueError):
        errors.append("readiness.minimum_approved_valid_rows must be an integer")
        min_approved = 300
    accepted_rows = int(validation.get("accepted_rows", 0)) if isinstance(validation, Mapping) else 0
    if accepted_rows >= min_approved:
        checks["approved_pilot_minimum"] = True
    else:
        errors.append(f"approved valid Chat V1 rows {accepted_rows} are below the required pilot minimum {min_approved}")

    quality_failures = validation.get("quality_failure_categories") if isinstance(validation, Mapping) else None
    generic_failures = validation.get("generic_assistant_style_failures") if isinstance(validation, Mapping) else None
    if not quality_failures and not generic_failures:
        checks["quality_failure_counters_zero"] = True
    else:
        errors.append("quality report has assistant-like/style failure counters; resolve the rejected source records before training")
    leakage_failures = validation.get("leakage_failures") if isinstance(validation, Mapping) else None
    if not leakage_failures:
        checks["leakage_failure_counter_zero"] = True
    else:
        errors.append("validation report has runtime identifier/leakage failures")

    required_categories = gates.get("required_categories", [])
    if not isinstance(required_categories, list) or any(not isinstance(item, str) for item in required_categories):
        errors.append("readiness.required_categories must be a string list")
        required_categories = []
    observed_categories = validation.get("category_distribution") if isinstance(validation, Mapping) and isinstance(validation.get("category_distribution"), Mapping) else {}
    missing_categories = sorted(category for category in required_categories if int(observed_categories.get(category, 0)) <= 0)
    if not missing_categories:
        checks["required_categories_covered"] = True
    else:
        errors.append("required Chat V1 categories missing: " + ", ".join(missing_categories))

    required_length_bands = gates.get("required_length_bands", [])
    if not isinstance(required_length_bands, list) or any(not isinstance(item, str) for item in required_length_bands):
        errors.append("readiness.required_length_bands must be a string list")
        required_length_bands = []
    observed_bands = validation.get("length_band_distribution") if isinstance(validation, Mapping) and isinstance(validation.get("length_band_distribution"), Mapping) else {}
    missing_length_bands = sorted(band for band in required_length_bands if int(observed_bands.get(band, 0)) <= 0)
    if not missing_length_bands:
        checks["required_length_bands_covered"] = True
    else:
        errors.append("required Chat V1 reply length bands missing: " + ", ".join(missing_length_bands))

    if split is not None:
        split_checks = split.get("checks") if isinstance(split.get("checks"), Mapping) else {}
        for key in ("player_group_disjoint", "thread_group_disjoint", "variant_group_disjoint", "processed_prompt_completion_shape_valid"):
            if split_checks.get(key) is not True:
                errors.append(f"split report did not assert {key}")
    checks["all_required_checks"] = not errors and all(checks.values())
    return {
        "ready": checks["all_required_checks"],
        "errors": list(dict.fromkeys(errors)),
        "checks": checks,
        "counts": {
            "approved_valid_rows": accepted_rows,
            "required_minimum": min_approved,
            "target_pilot_rows": gates.get("target_pilot_rows", 500),
            "split": split_counts if isinstance(split_counts, Mapping) else {},
            "quality_failure_categories": quality_failures or {},
            "generic_assistant_style_failures": generic_failures or {},
            "leakage_failures": leakage_failures or {},
            "missing_categories": missing_categories,
            "missing_length_bands": missing_length_bands,
        },
        "reports": {
            "action_v1_comparison": str(action_report_path),
            "validation": str(validation_path),
            "leakage": str(leakage_path),
            "split": str(split_path),
        },
        "dataset_paths": {"train": str(train_path), "validation": str(validation_data_path), "test": str(test_path)},
        "hashes": current_hashes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true", help="Evaluate but do not write the readiness report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = safe_within_training(args.config if args.config.is_absolute() else TRAINING_ROOT / args.config)
    config = _load_json(config_path, "Chat V1 configuration")
    result = evaluate_readiness(config)
    report = {
        "schema_version": 1,
        "stage": "chat_v1_readiness",
        "created_at": utc_now(),
        "config_path": str(config_path),
        "status": "CHAT V1 TRAINING READY" if result["ready"] else "CHAT V1 TRAINING BLOCKED",
        **result,
    }
    if not args.dry_run:
        report_path = write_json(args.report, report)
        report["report_path"] = str(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if result["ready"]:
        print("CHAT V1 TRAINING READY")
        return 0
    print("CHAT V1 TRAINING BLOCKED")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DatasetError, ReadinessError, ValueError) as exc:
        print(f"chat readiness failed: {exc}", file=sys.stderr)
        raise SystemExit(2)

