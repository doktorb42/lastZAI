#!/usr/bin/env python3
"""Keep Chat V1 training rows disjoint from protected chat benchmark fixtures.

Only this scanner reads benchmark files, and it never writes to them or uses
their labels to generate an answer.  It rejects exact model-input overlaps and
exact protected answer overlaps before a future train/validation/test split.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from chat_v1_contract import canonical_json, prompt_key, target_key
from lastz_model_benchmark.conversation_system.lastz_conversation.contracts import (
    ContractError,
    build_task_chat_input,
)
from dataset_common import (
    BENCHMARK_CASES_DIR,
    DATA_ROOT,
    DatasetError,
    add_rejection,
    read_jsonl,
    sha256_file,
    utc_now,
    write_json,
    write_jsonl,
)


DEFAULT_INPUT = DATA_ROOT / "reviewed" / "chat_v1_validated.jsonl"
DEFAULT_OUTPUT = DATA_ROOT / "processed" / "chat_v1_leak_checked.jsonl"
DEFAULT_REJECTED = DATA_ROOT / "rejected" / "chat_v1_benchmark_leakage_rejected.jsonl"
DEFAULT_REPORT = DATA_ROOT / "reports" / "chat_v1_benchmark_leakage_report.json"


@dataclass(frozen=True)
class FixtureSurface:
    source: str
    fixture_id: str
    prompt_fingerprints: frozenset[str]
    target_fingerprints: frozenset[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--cases-dir", type=Path, default=BENCHMARK_CASES_DIR)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        texts: list[str] = []
        for nested in value.values():
            texts.extend(_text_values(nested))
        return texts
    if isinstance(value, list):
        texts = []
        for nested in value:
            texts.extend(_text_values(nested))
        return texts
    return []


def _fixture_prompt_fingerprints(row: Mapping[str, Any]) -> set[str]:
    values: list[Any] = []
    for field in ("task_snapshot", "task_input", "input", "context", "canonical_context", "prompt", "messages", "conversation"):
        if field in row:
            value = row[field]
            if field == "task_snapshot" and isinstance(value, Mapping) and "input" in value:
                value = value["input"]
            # Chat V1 quality fixtures carry a V2 context, not an ad-hoc
            # prompt.  Turn it into the exact TASK_CHAT input before hashing
            # so a future training row cannot overlap it unnoticed.
            if field == "canonical_context":
                try:
                    value = build_task_chat_input(value)
                except (ContractError, TypeError, ValueError):
                    continue
            values.append(value)
    fingerprints: set[str] = set()
    for value in values:
        try:
            fingerprints.add(prompt_key({"task_snapshot": {"input": value}}))
        except (TypeError, ValueError):
            pass
        try:
            fingerprints.add(target_key(canonical_json(value)))
        except (TypeError, ValueError):
            pass
    return {value for value in fingerprints if value}


def _fixture_target_fingerprints(row: Mapping[str, Any]) -> set[str]:
    values: list[Any] = []
    # Avoid ``required_concepts`` and other broad rubric tokens: only an exact
    # answer/reference label is protected as a target overlap.
    for field in ("expected", "expected_response", "reference", "reference_response", "target", "gold_response", "approved_reply"):
        if field in row:
            values.append(row[field])
    return {target_key(text) for value in values for text in _text_values(value) if text.strip()}


def fixture_surfaces(cases_dir: Path) -> tuple[list[FixtureSurface], dict[str, int]]:
    root = cases_dir.expanduser().resolve()
    if not root.is_dir():
        raise DatasetError(f"benchmark cases directory not found: {root}")
    fixtures: list[FixtureSurface] = []
    files: dict[str, int] = {}
    # Action fixtures do not expose Chat V1 prompts.  Limit the scan to chat
    # fixture names so adding a new action fixture cannot cause false positives.
    for path in sorted(root.rglob("*chat*.jsonl")):
        rows = read_jsonl(path)
        files[str(path)] = len(rows)
        for line, row in enumerate(rows, 1):
            fixtures.append(
                FixtureSurface(
                    source=str(path),
                    fixture_id=str(row.get("id", f"line-{line}")),
                    prompt_fingerprints=frozenset(_fixture_prompt_fingerprints(row)),
                    target_fingerprints=frozenset(_fixture_target_fingerprints(row)),
                )
            )
    if not fixtures:
        raise DatasetError(f"no chat benchmark fixture JSONL files found under {root}")
    return fixtures, files


def _source_prompt_fingerprints(row: Mapping[str, Any]) -> set[str]:
    snapshot = row.get("task_snapshot")
    if not isinstance(snapshot, Mapping):
        return set()
    task_input = snapshot.get("input")
    fingerprints = {prompt_key(row)}
    try:
        fingerprints.add(target_key(canonical_json(task_input)))
    except (TypeError, ValueError):
        pass
    return {value for value in fingerprints if value}


def check_rows(rows: list[dict[str, Any]], fixtures: list[FixtureSurface]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        source_prompts = _source_prompt_fingerprints(row)
        reply = row.get("approved_reply")
        source_target = target_key(reply) if isinstance(reply, str) else ""
        match: FixtureSurface | None = None
        reason: str | None = None
        for fixture in fixtures:
            if source_prompts & fixture.prompt_fingerprints:
                match, reason = fixture, "canonical chat benchmark prompt overlap"
                break
            if source_target and source_target in fixture.target_fingerprints:
                match, reason = fixture, "canonical chat benchmark target overlap"
                break
        if match is None or reason is None:
            accepted.append(row)
            continue
        reasons[reason] += 1
        rejected.append(
            add_rejection(
                copy.deepcopy(row),
                [f"{reason}; fixture={Path(match.source).name}:{match.fixture_id}"],
                "check_chat_v1_leakage",
            )
        )
    return accepted, rejected, dict(sorted(reasons.items()))


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    fixtures, files = fixture_surfaces(args.cases_dir)
    accepted, rejected, reasons = check_rows(rows, fixtures)
    report = {
        "schema_version": 1,
        "stage": "check_chat_v1_leakage",
        "created_at": utc_now(),
        "input": {"path": str(args.input.expanduser().resolve()), "sha256": sha256_file(args.input), "rows": len(rows)},
        "benchmark_cases_dir": str(args.cases_dir.expanduser().resolve()),
        "fixture_files_scanned": files,
        "fixture_rows_scanned": len(fixtures),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "rejection_reasons": reasons,
        "passed": bool(accepted) and not rejected,
        "critical_errors": (["no rows remain after protected benchmark leakage scan"] if not accepted else []) + (["one or more rows overlap protected chat benchmark fixtures"] if rejected else []),
        "checks": {
            "chat_benchmark_fixture_scan_completed": bool(files),
            "canonical_prompt_overlap_scan_completed": True,
            "protected_target_overlap_scan_completed": True,
            "leak_scan_passed": not rejected,
        },
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **report}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["passed"] else 2
    output = write_jsonl(args.output, accepted)
    rejected_path = write_jsonl(args.rejected, rejected)
    report["outputs"] = {
        "accepted": {"path": str(output), "sha256": sha256_file(output), "rows": len(accepted)},
        "rejected": {"path": str(rejected_path), "sha256": sha256_file(rejected_path), "rows": len(rejected)},
    }
    report_path = write_json(args.report, report)
    print(json.dumps({"report": str(report_path), **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatasetError as exc:
        raise SystemExit(f"chat leakage scan failed: {exc}")

