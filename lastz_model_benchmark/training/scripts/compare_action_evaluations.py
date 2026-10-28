#!/usr/bin/env python3
"""Compare two completed Last Z action checkpoint evaluations safely.

The tool only reads JSON reports emitted by ``evaluate_checkpoint.py`` and
writes a compact JSON/Markdown comparison below ``training/outputs/reports``.
It never loads a model, starts a server, imports game code, or contacts ADB /
BlueStacks.  Both input reports must be completed, unlimited evaluations of
the current full protected production and adversarial action suites.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
BUNDLE_ROOT = TRAINING_ROOT.parent
REPORTS_DIR = TRAINING_ROOT / "outputs" / "reports"
CASES_DIR = BUNDLE_ROOT / "benchmarks" / "cases"
EXPECTED_SUITES = ("production_action_suite", "adversarial_action_suite")
CORE_CASES = CASES_DIR / "decision_cases.jsonl"
PRODUCTION_CASES = CASES_DIR / "production_action_cases.jsonl"
ADVERSARIAL_CASES = CASES_DIR / "adversarial_action_cases.jsonl"


class ComparisonError(RuntimeError):
    """An input report is incomplete, incompatible, or outside the bundle."""


@dataclass(frozen=True)
class ValidatedReport:
    path: Path
    data: dict[str, Any]
    suite_ids: dict[str, tuple[str, ...]]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def percentile(values: list[float], fraction: float) -> float | None:
    """Match the interpolation/rounding used by evaluate_checkpoint.py."""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    value = ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 3)


def require_below(path: Path, parent: Path, label: str) -> Path:
    resolved = path.resolve()
    root = parent.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ComparisonError(f"{label} must remain below {root}; got {resolved}") from exc
    return resolved


def resolve_input_path(value: Path, label: str) -> Path:
    """Resolve a report name relative to the only allowed report directory."""

    requested = value if value.is_absolute() else REPORTS_DIR / value
    path = require_below(requested, REPORTS_DIR, label)
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ComparisonError(f"{label} must name an existing JSON report below {REPORTS_DIR}: {path}")
    return path


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError(f"{label} must contain a JSON object")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ComparisonError(f"protected fixture is missing: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ComparisonError(f"invalid protected fixture {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ComparisonError(f"invalid protected fixture case at {path}:{line_number}")
        rows.append(row)
    return rows


def protected_suites() -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """Return the canonical current IDs and content fingerprints without a runner import."""

    # This mirrors load_action_suites(): core cases take precedence when a
    # documentation mirror in production_action_cases has the same ID.
    production_by_id: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(CORE_CASES):
        if row["id"] in production_by_id:
            raise ComparisonError(f"duplicate core protected case ID: {row['id']}")
        production_by_id[row["id"]] = row
    for row in read_jsonl(PRODUCTION_CASES):
        production_by_id.setdefault(row["id"], row)
    adversarial_rows = read_jsonl(ADVERSARIAL_CASES)
    adversarial_ids = [row["id"] for row in adversarial_rows]
    if len(adversarial_ids) != len(set(adversarial_ids)):
        raise ComparisonError("duplicate adversarial protected case ID")
    production_ids = tuple(production_by_id)
    if set(production_ids) & set(adversarial_ids):
        raise ComparisonError("protected production and adversarial suites overlap")

    def fingerprint(rows: list[dict[str, Any]]) -> str:
        canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return (
        {
            "production_action_suite": production_ids,
            "adversarial_action_suite": tuple(adversarial_ids),
        },
        {
            "production_action_suite": fingerprint(list(production_by_id.values())),
            "adversarial_action_suite": fingerprint(adversarial_rows),
        },
    )


def finite_number(value: Any, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ComparisonError(f"{label} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ComparisonError(f"{label} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ComparisonError(f"{label} must be <= {maximum}")
    return result


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ComparisonError(f"{label} must be boolean")
    return value


def validate_suite(
    suite_name: str,
    suite: Any,
    expected_ids: tuple[str, ...],
    expected_fixture_sha256: str,
    rounds: int,
    source_label: str,
) -> tuple[str, ...]:
    if not isinstance(suite, dict):
        raise ComparisonError(f"{source_label}.{suite_name} must be an object")
    if suite.get("suite") not in (None, suite_name):
        raise ComparisonError(f"{source_label}.{suite_name}.suite is inconsistent")
    case_count = suite.get("case_count")
    if isinstance(case_count, bool) or not isinstance(case_count, int) or case_count != len(expected_ids):
        raise ComparisonError(
            f"{source_label}.{suite_name}.case_count must equal the full protected count {len(expected_ids)}"
        )
    fingerprint = suite.get("fixture_sha256")
    if not isinstance(fingerprint, str) or fingerprint != expected_fixture_sha256:
        raise ComparisonError(
            f"{source_label}.{suite_name}.fixture_sha256 must match the current protected fixture content"
        )
    metrics = suite.get("metrics")
    samples = suite.get("samples")
    if not isinstance(metrics, dict) or not isinstance(samples, list):
        raise ComparisonError(f"{source_label}.{suite_name} needs completed metrics and samples")
    expected_sample_count = len(expected_ids) * rounds
    if len(samples) != expected_sample_count:
        raise ComparisonError(
            f"{source_label}.{suite_name} has {len(samples)} samples; full evaluation needs {expected_sample_count}"
        )
    if metrics.get("count") != expected_sample_count:
        raise ComparisonError(f"{source_label}.{suite_name}.metrics.count is inconsistent with samples")

    expected_set = set(expected_ids)
    by_round: dict[int, set[str]] = {number: set() for number in range(1, rounds + 1)}
    bool_fields = {
        "json_rate": "valid_json",
        "schema_rate": "schema_valid",
        "legal_rate": "legal_action",
        "exact_rate": "exact_match",
    }
    success_counts = {metric: 0 for metric in bool_fields}
    latencies: list[float] = []
    for index, sample in enumerate(samples, 1):
        prefix = f"{source_label}.{suite_name}.samples[{index}]"
        if not isinstance(sample, dict):
            raise ComparisonError(f"{prefix} must be an object")
        case_id = sample.get("case_id")
        if not isinstance(case_id, str) or case_id not in expected_set:
            raise ComparisonError(f"{prefix}.case_id is not in the protected {suite_name}")
        round_number = sample.get("round")
        if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number not in by_round:
            raise ComparisonError(f"{prefix}.round is outside 1..{rounds}")
        if case_id in by_round[round_number]:
            raise ComparisonError(f"{prefix} repeats {case_id!r} in round {round_number}")
        by_round[round_number].add(case_id)
        for metric, field in bool_fields.items():
            if require_bool(sample.get(field), f"{prefix}.{field}"):
                success_counts[metric] += 1
        latencies.append(finite_number(sample.get("latency_ms"), f"{prefix}.latency_ms", minimum=0.0))
    for round_number, case_ids in by_round.items():
        if case_ids != expected_set:
            missing = sorted(expected_set - case_ids)
            extras = sorted(case_ids - expected_set)
            raise ComparisonError(
                f"{source_label}.{suite_name} round {round_number} is not the full protected suite "
                f"(missing={missing}, extras={extras})"
            )

    for metric, field in bool_fields.items():
        actual = finite_number(metrics.get(metric), f"{source_label}.{suite_name}.metrics.{metric}", minimum=0.0, maximum=1.0)
        expected = success_counts[metric] / expected_sample_count
        if not math.isclose(actual, expected, abs_tol=1e-12):
            raise ComparisonError(
                f"{source_label}.{suite_name}.metrics.{metric} does not match sample {field} values"
            )
    robustness = finite_number(metrics.get("robustness_rate"), f"{source_label}.{suite_name}.metrics.robustness_rate", minimum=0.0, maximum=1.0)
    exact_rate = success_counts["exact_rate"] / expected_sample_count
    if not math.isclose(robustness, exact_rate, abs_tol=1e-12):
        raise ComparisonError(f"{source_label}.{suite_name}.metrics.robustness_rate must equal exact-match rate")

    latency = metrics.get("latency_ms")
    if not isinstance(latency, dict):
        raise ComparisonError(f"{source_label}.{suite_name}.metrics.latency_ms must be an object")
    for name, fraction in (("p50", 0.50), ("p95", 0.95)):
        actual = finite_number(latency.get(name), f"{source_label}.{suite_name}.metrics.latency_ms.{name}", minimum=0.0)
        expected = percentile(latencies, fraction)
        if expected is None or not math.isclose(actual, expected, abs_tol=0.001):
            raise ComparisonError(
                f"{source_label}.{suite_name}.metrics.latency_ms.{name} does not match sample latencies"
            )
    return tuple(expected_ids)


def validate_report(
    data: dict[str, Any],
    path: Path,
    expected: dict[str, tuple[str, ...]],
    fingerprints: dict[str, str],
    label: str,
) -> ValidatedReport:
    if data.get("status") != "passed":
        raise ComparisonError(f"{label} must be a completed report with status='passed', not {data.get('status')!r}")
    # ``limit`` must be explicit: accepting an old/malformed report with this
    # field absent would make it impossible to prove full-suite coverage.
    if "limit" not in data or data["limit"] is not None:
        raise ComparisonError(f"{label} must contain explicit limit: null; limited reports cannot be compared")
    if data.get("holdout_usage") != "evaluation only; never a training data source":
        raise ComparisonError(f"{label} has an unexpected holdout_usage declaration")
    rounds = data.get("rounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ComparisonError(f"{label}.rounds must be a positive integer")
    maximum = data.get("max_new_tokens")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 24:
        raise ComparisonError(f"{label}.max_new_tokens must be an integer in 1..24")
    suites = data.get("suites")
    if not isinstance(suites, dict) or set(suites) != set(EXPECTED_SUITES):
        raise ComparisonError(f"{label} must contain exactly the two protected action suites")
    suite_ids = {
        name: validate_suite(name, suites[name], expected[name], fingerprints[name], rounds, label)
        for name in EXPECTED_SUITES
    }
    return ValidatedReport(path=path, data=data, suite_ids=suite_ids)


def require_matching_protocol(base: ValidatedReport, fine: ValidatedReport) -> None:
    """Make changes attributable to the checkpoint rather than test settings."""

    for field in ("rounds", "max_new_tokens", "seed"):
        if base.data.get(field) != fine.data.get(field):
            raise ComparisonError(
                f"base and fine reports use different {field}: {base.data.get(field)!r} vs {fine.data.get(field)!r}"
            )
    if base.suite_ids != fine.suite_ids:
        raise ComparisonError("base and fine reports did not cover the same protected case IDs")


def rate_delta(base: float, fine: float) -> dict[str, float]:
    return {
        "base": base,
        "fine_tuned": fine,
        "delta_percentage_points": round((fine - base) * 100.0, 3),
    }


def latency_delta(base: float, fine: float) -> dict[str, float | None]:
    delta = fine - base
    return {
        "base_ms": base,
        "fine_tuned_ms": fine,
        "delta_ms": round(delta, 3),
        "relative_change_percent": round((delta / base) * 100.0, 3) if base else None,
    }


def strict_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    conditions = {
        "schema_valid_100": metrics.get("schema_rate") == 1.0,
        "legal_100": metrics.get("legal_rate") == 1.0,
        "exact_100": metrics.get("exact_rate") == 1.0,
    }
    return {
        **conditions,
        "qualifies": all(conditions.values()),
        "definition": "all full protected production cases are schema-valid, legal, and exact",
        "scope_note": "This compares unconstrained HF checkpoint holdout reports. Final production promotion still requires the separate CUDA llama.cpp Q6_K schema-constrained benchmark.",
    }


def model_identity(data: dict[str, Any]) -> dict[str, Any]:
    """Keep report provenance useful without interpreting or loading a model."""

    return {
        "model": data.get("model"),
        "revision": data.get("revision"),
        "adapter": data.get("adapter"),
        "adapter_provenance": data.get("adapter_provenance"),
        "model_source": data.get("model_source"),
    }


def build_comparison(
    base: ValidatedReport,
    fine: ValidatedReport,
    fingerprints: dict[str, str],
) -> dict[str, Any]:
    require_matching_protocol(base, fine)
    base_suites = base.data["suites"]
    fine_suites = fine.data["suites"]
    production_base = base_suites["production_action_suite"]["metrics"]
    production_fine = fine_suites["production_action_suite"]["metrics"]
    adversarial_base = base_suites["adversarial_action_suite"]["metrics"]
    adversarial_fine = fine_suites["adversarial_action_suite"]["metrics"]
    suite_counts = {name: len(ids) for name, ids in base.suite_ids.items()}

    latency: dict[str, Any] = {}
    for suite, initial, adapted in (
        ("production_action_suite", production_base, production_fine),
        ("adversarial_action_suite", adversarial_base, adversarial_fine),
    ):
        latency[suite] = {
            "p50": latency_delta(float(initial["latency_ms"]["p50"]), float(adapted["latency_ms"]["p50"])),
            "p95": latency_delta(float(initial["latency_ms"]["p95"]), float(adapted["latency_ms"]["p95"])),
        }

    return {
        "kind": "lastz_action_evaluation_comparison_v1",
        "created_at": utc_now(),
        "status": "passed",
        "safety": {
            "model_execution": "not performed",
            "game_or_adb_access": "not performed",
            "inputs": "completed evaluate_checkpoint.py JSON reports only",
        },
        "inputs": {
            "base_report": str(base.path),
            "fine_tuned_report": str(fine.path),
            "base_model": model_identity(base.data),
            "fine_tuned_model": model_identity(fine.data),
            "protocol": {
                "rounds": base.data["rounds"],
                "max_new_tokens": base.data["max_new_tokens"],
                "seed": base.data.get("seed"),
            },
            "protected_suite_case_counts": suite_counts,
            "protected_fixture_sha256": fingerprints,
            "full_suite_verified": True,
            "unlimited_reports_verified": True,
        },
        "production_action": {
            "schema_valid_rate": rate_delta(float(production_base["schema_rate"]), float(production_fine["schema_rate"])),
            "legal_action_rate": rate_delta(float(production_base["legal_rate"]), float(production_fine["legal_rate"])),
            "exact_decision_rate": rate_delta(float(production_base["exact_rate"]), float(production_fine["exact_rate"])),
        },
        "adversarial_action": {
            "robustness_rate": rate_delta(float(adversarial_base["robustness_rate"]), float(adversarial_fine["robustness_rate"])),
            "schema_valid_rate": rate_delta(float(adversarial_base["schema_rate"]), float(adversarial_fine["schema_rate"])),
            "legal_action_rate": rate_delta(float(adversarial_base["legal_rate"]), float(adversarial_fine["legal_rate"])),
        },
        "latency": latency,
        "fine_tuned_strict_production_qualification": strict_gate(production_fine),
    }


def rate_text(value: float) -> str:
    return f"{value * 100.0:.1f}%"


def signed(value: float, suffix: str = "") -> str:
    return f"{value:+.3f}{suffix}"


def markdown(report: dict[str, Any]) -> str:
    inputs = report["inputs"]
    production = report["production_action"]
    adversarial = report["adversarial_action"]
    qualification = report["fine_tuned_strict_production_qualification"]
    lines = [
        "# Last Z action checkpoint comparison",
        "",
        f"- Status: `{report['status']}`",
        "- Evaluation scope: full protected production and adversarial suites; no per-suite limit.",
        f"- Protocol: {inputs['protocol']['rounds']} round(s), max {inputs['protocol']['max_new_tokens']} new token(s), seed {inputs['protocol']['seed']}.",
        f"- Base report: `{inputs['base_report']}`",
        f"- Fine-tuned report: `{inputs['fine_tuned_report']}`",
        f"- Base model: `{inputs['base_model']['model']}`; adapter: `{inputs['base_model']['adapter'] or 'base only'}`",
        f"- Fine-tuned model: `{inputs['fine_tuned_model']['model']}`; adapter: `{inputs['fine_tuned_model']['adapter'] or 'base only'}`",
        "",
        "## Production action",
        "",
        "| Metric | Base | Fine-tuned | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, key in (
        ("Schema-valid", "schema_valid_rate"),
        ("Legal action", "legal_action_rate"),
        ("Exact decision", "exact_decision_rate"),
    ):
        metric = production[key]
        lines.append(
            f"| {label} | {rate_text(metric['base'])} | {rate_text(metric['fine_tuned'])} | "
            f"{signed(metric['delta_percentage_points'], ' pp')} |"
        )
    lines.extend([
        "",
        "## Adversarial action",
        "",
        "| Metric | Base | Fine-tuned | Delta |",
        "| --- | ---: | ---: | ---: |",
    ])
    metric = adversarial["robustness_rate"]
    lines.append(
        f"| Robustness / exact-safe decision | {rate_text(metric['base'])} | {rate_text(metric['fine_tuned'])} | "
        f"{signed(metric['delta_percentage_points'], ' pp')} |"
    )
    lines.extend([
        "",
        "## Latency",
        "",
        "Negative deltas are faster for the fine-tuned checkpoint.",
        "",
        "| Suite | Percentile | Base | Fine-tuned | Delta |",
        "| --- | --- | ---: | ---: | ---: |",
    ])
    for suite_label, suite in (("Production", "production_action_suite"), ("Adversarial", "adversarial_action_suite")):
        for percentile_name in ("p50", "p95"):
            metric = report["latency"][suite][percentile_name]
            lines.append(
                f"| {suite_label} | {percentile_name} | {metric['base_ms']:.3f} ms | "
                f"{metric['fine_tuned_ms']:.3f} ms | {signed(metric['delta_ms'], ' ms')} |"
            )
    verdict = "PASS" if qualification["qualifies"] else "FAIL"
    lines.extend([
        "",
        "## Strict production qualification",
        "",
        f"**{verdict}** — fine-tuned model qualifies: `{qualification['qualifies']}`.",
        "",
        f"- Schema-valid 100%: `{qualification['schema_valid_100']}`",
        f"- Legal 100%: `{qualification['legal_100']}`",
        f"- Exact 100%: `{qualification['exact_100']}`",
        f"- Note: {qualification['scope_note']}",
        "",
    ])
    return "\n".join(lines)


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except FileExistsError as exc:
        raise ComparisonError(f"refusing to overwrite an existing report: {path}") from exc


def synthetic_report(
    expected: dict[str, tuple[str, ...]], fingerprints: dict[str, str], latency_offset: float
) -> dict[str, Any]:
    """Build a valid in-memory report for ``--self-test``; it writes nothing."""

    suites: dict[str, Any] = {}
    for suite_number, (suite_name, ids) in enumerate(expected.items(), 1):
        samples = [
            {
                "round": 1,
                "case_id": case_id,
                "valid_json": True,
                "schema_valid": True,
                "legal_action": True,
                "exact_match": True,
                "latency_ms": float(index + suite_number + latency_offset),
            }
            for index, case_id in enumerate(ids, 1)
        ]
        latencies = [sample["latency_ms"] for sample in samples]
        suites[suite_name] = {
            "suite": suite_name,
            "case_count": len(ids),
            "fixture_sha256": fingerprints[suite_name],
            "metrics": {
                "count": len(samples),
                "json_rate": 1.0,
                "schema_rate": 1.0,
                "legal_rate": 1.0,
                "exact_rate": 1.0,
                "robustness_rate": 1.0,
                "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95)},
            },
            "samples": samples,
        }
    return {
        "status": "passed",
        "limit": None,
        "holdout_usage": "evaluation only; never a training data source",
        "rounds": 1,
        "max_new_tokens": 24,
        "seed": 20261010,
        "suites": suites,
    }


def run_self_test() -> int:
    expected, fingerprints = protected_suites()
    base = ValidatedReport(
        path=REPORTS_DIR / "synthetic_base.json",
        data=synthetic_report(expected, fingerprints, 10.0),
        suite_ids={},
    )
    fine = ValidatedReport(
        path=REPORTS_DIR / "synthetic_fine.json",
        data=synthetic_report(expected, fingerprints, 5.0),
        suite_ids={},
    )
    base = validate_report(base.data, base.path, expected, fingerprints, "synthetic base report")
    fine = validate_report(fine.data, fine.path, expected, fingerprints, "synthetic fine report")
    result = build_comparison(base, fine, fingerprints)
    if not result["fine_tuned_strict_production_qualification"]["qualifies"]:
        raise ComparisonError("self-test did not pass strict production qualification")
    if result["latency"]["production_action_suite"]["p50"]["delta_ms"] >= 0:
        raise ComparisonError("self-test did not preserve negative fine-tuned latency delta")
    if "Strict production qualification" not in markdown(result):
        raise ComparisonError("self-test could not render the Markdown comparison")
    print("[OK] synthetic comparison validation passed (no files written, no model or game access).")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-report", type=Path, help="Completed base evaluation JSON, relative to outputs/reports or absolute below it.")
    parser.add_argument("--fine-report", type=Path, help="Completed fine-tuned evaluation JSON, relative to outputs/reports or absolute below it.")
    parser.add_argument(
        "--output-stem",
        help="Optional safe filename stem below outputs/reports; default includes a UTC timestamp.",
    )
    parser.add_argument("--self-test", action="store_true", help="Validate comparison logic in memory without reading reports or writing files.")
    args = parser.parse_args()
    if args.self_test:
        if args.base_report or args.fine_report or args.output_stem:
            parser.error("--self-test cannot be combined with report or output arguments")
    elif not args.base_report or not args.fine_report:
        parser.error("--base-report and --fine-report are required unless --self-test is used")
    if args.output_stem and (not args.output_stem.replace("-", "").replace("_", "").replace(".", "").isalnum()):
        parser.error("--output-stem may contain only letters, digits, '.', '_' and '-'")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            return run_self_test()
        expected, fingerprints = protected_suites()
        base_path = resolve_input_path(args.base_report, "--base-report")
        fine_path = resolve_input_path(args.fine_report, "--fine-report")
        if base_path == fine_path:
            raise ComparisonError("base and fine reports must be different files")
        base = validate_report(read_json(base_path, "base report"), base_path, expected, fingerprints, "base report")
        fine = validate_report(read_json(fine_path, "fine report"), fine_path, expected, fingerprints, "fine-tuned report")
        report = build_comparison(base, fine, fingerprints)
        markdown_text = markdown(report)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        stem = args.output_stem or f"action_evaluation_comparison_{stamp}"
        json_path = require_below(REPORTS_DIR / f"{stem}.json", REPORTS_DIR, "comparison output")
        markdown_path = require_below(REPORTS_DIR / f"{stem}.md", REPORTS_DIR, "comparison output")
        if json_path == base_path or json_path == fine_path or markdown_path == base_path or markdown_path == fine_path:
            raise ComparisonError("comparison output must not overwrite an input report")
        write_new(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        try:
            write_new(markdown_path, markdown_text)
        except Exception:
            # The JSON comparison is a valid audit artefact if Markdown I/O
            # fails, so leave it intact rather than overwriting anything.
            raise
        print(f"[OK] comparison JSON: {json_path}")
        print(f"[OK] comparison Markdown: {markdown_path}")
        return 0
    except ComparisonError as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
