#!/usr/bin/env python3
"""Block synthetic action rows that overlap any benchmark/cases JSONL input.

Fixture output labels are deliberately never loaded into a comparison surface.
Only input-side state, memory, capabilities, planner handoffs, policy signals,
and untrusted observations are used to identify direct or near benchmark
leakage.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dataset_common import (
    BENCHMARK_CASES_DIR,
    DATA_ROOT,
    DatasetError,
    add_rejection,
    canonical_json,
    character_ngrams,
    jaccard_similarity,
    normalise_text,
    read_jsonl,
    sha256_file,
    utc_now,
    write_json,
    write_jsonl,
)
from lastz_action_contract import (
    action_input_payload,
    benchmark_input_projection,
    derive_policy_signals,
    normalise_descriptor,
    render_user_prompt,
    structural_signature,
)


DEFAULT_INPUT = DATA_ROOT / "processed" / "action_v1_deduplicated.jsonl"
DEFAULT_OUTPUT = DATA_ROOT / "processed" / "action_v1_leak_checked.jsonl"
DEFAULT_REJECTED = DATA_ROOT / "rejected" / "action_v1_benchmark_leakage_rejected.jsonl"
DEFAULT_REPORT = DATA_ROOT / "reports" / "action_v1_benchmark_leakage_report.json"


@dataclass(frozen=True)
class FixtureSurface:
    source: str
    fixture_id: str
    canonical_prompt: str
    semantic_key: str
    structural_key: str
    broad_key: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rejected", type=Path, default=DEFAULT_REJECTED)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--cases-dir", type=Path, default=BENCHMARK_CASES_DIR)
    parser.add_argument("--near-threshold", type=float, default=0.94)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 0.80 <= args.near_threshold <= 1.0:
        parser.error("--near-threshold must be in [0.80, 1.0]")
    return args


def _semantic_projection(case: dict[str, Any]) -> dict[str, Any]:
    """Drop capture noise while retaining all action-relevant prompt facts."""

    raw = benchmark_input_projection(case)
    state = raw.get("state") if isinstance(raw.get("state"), dict) else {}
    unread = state.get("unread") if isinstance(state.get("unread"), dict) else {}
    memory = raw.get("agent_memory") if isinstance(raw.get("agent_memory"), dict) else {}

    def descriptors(field: str, *, virtual: bool = False) -> list[dict[str, Any]]:
        source = raw.get(field)
        normalized: list[dict[str, Any]] = []
        if isinstance(source, list):
            for item in source:
                value = normalise_descriptor(item, allow_virtual=virtual)
                if value is not None:
                    normalized.append(value)
                elif isinstance(item, dict):
                    # Unknown descriptor names are still relevant for leakage
                    # shape but cannot grant an action in the contract.
                    normalized.append({"action": "<unknown>", "keys": sorted(map(str, item))})
        return sorted(normalized, key=canonical_json)

    observations = raw.get("untrusted_observations")
    normalized_observations = (
        sorted(normalise_text(item) for item in observations if isinstance(item, str))
        if isinstance(observations, list)
        else []
    )
    signals = derive_policy_signals(case)
    return {
        "state": {
            "screen": state.get("screen"),
            "unread": {key: bool(unread.get(key)) for key in ("private", "alliance", "chat")},
        },
        "agent_memory": {"pending_private_message": bool(memory.get("pending_private_message"))},
        "policy_signals": {
            key: bool(signals.get(key))
            for key in (
                "screen_supported",
                "chat_open_priority",
                "private_route_pending",
                "reply_authorized",
                "reply_context_available",
            )
        },
        "available_actions": descriptors("available_actions"),
        "planner_actions": descriptors("planner_actions", virtual=True),
        "untrusted_observations": normalized_observations,
    }


def _broad_key(case: dict[str, Any]) -> str:
    signature = structural_signature(case)
    actions = signature.get("available_actions", [])
    action_names = sorted(
        item.get("action", "<unknown>") for item in actions if isinstance(item, dict)
    )
    return canonical_json({"screen": signature.get("screen"), "actions": action_names})


def _fixture_surfaces(cases_dir: Path) -> tuple[list[FixtureSurface], dict[str, int]]:
    root = cases_dir.expanduser().resolve()
    if not root.is_dir():
        raise DatasetError(f"benchmark cases directory not found: {root}")
    surfaces: list[FixtureSurface] = []
    files: dict[str, int] = {}
    for path in sorted(root.rglob("*.jsonl")):
        rows = read_jsonl(path)
        files[str(path)] = len(rows)
        for line_number, row in enumerate(rows, 1):
            # No fixture target/candidate/legality field is consulted here.
            # benchmark_input_projection selects only safe input-side fields.
            fixture_id = str(row.get("id", f"line-{line_number}"))
            surfaces.append(
                FixtureSurface(
                    source=str(path),
                    fixture_id=fixture_id,
                    canonical_prompt=render_user_prompt(row),
                    semantic_key=canonical_json(_semantic_projection(row)),
                    structural_key=canonical_json(structural_signature(row)),
                    broad_key=_broad_key(row),
                )
            )
    if not surfaces:
        raise DatasetError(f"no JSONL fixtures found under {root}")
    return surfaces, files


def check_rows(
    rows: list[dict[str, Any]], fixtures: list[FixtureSurface], near_threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    by_prompt: dict[str, list[FixtureSurface]] = defaultdict(list)
    by_semantic: dict[str, list[FixtureSurface]] = defaultdict(list)
    by_structure: dict[str, list[FixtureSurface]] = defaultdict(list)
    by_broad: dict[str, list[FixtureSurface]] = defaultdict(list)
    for fixture in fixtures:
        by_prompt[fixture.canonical_prompt].append(fixture)
        by_semantic[fixture.semantic_key].append(fixture)
        by_structure[fixture.structural_key].append(fixture)
        by_broad[fixture.broad_key].append(fixture)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row in rows:
        prompt = render_user_prompt(row)
        semantic = canonical_json(_semantic_projection(row))
        structure = canonical_json(structural_signature(row))
        broad = _broad_key(row)
        match: FixtureSurface | None = None
        reason: str | None = None
        if by_prompt.get(prompt):
            match, reason = by_prompt[prompt][0], "canonical benchmark prompt match"
        elif by_semantic.get(semantic):
            match, reason = by_semantic[semantic][0], "semantic benchmark input match"
        elif by_structure.get(structure):
            # Structure equality deliberately catches a renamed capture ID or
            # detector-noise variant of a held-out benchmark scenario.
            match, reason = by_structure[structure][0], "structural benchmark input match"
        else:
            best_score = 0.0
            for fixture in by_broad.get(broad, []):
                score = jaccard_similarity(character_ngrams(prompt), character_ngrams(fixture.canonical_prompt))
                if score > best_score:
                    best_score, match = score, fixture
            if match is not None and best_score >= near_threshold:
                reason = f"near benchmark prompt match (similarity={best_score:.4f})"
            else:
                match = None
        if match is None or reason is None:
            accepted.append(row)
            continue
        counts[reason.split(" (")[0]] += 1
        rejected.append(
            add_rejection(
                copy.deepcopy(row),
                [f"{reason}; fixture={Path(match.source).name}:{match.fixture_id}"],
                "check_benchmark_leakage",
            )
        )
    return accepted, rejected, dict(sorted(counts.items()))


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    fixtures, files = _fixture_surfaces(args.cases_dir)
    accepted, rejected, reasons = check_rows(rows, fixtures, args.near_threshold)
    report = {
        "schema_version": 1,
        "stage": "check_benchmark_leakage",
        "created_at": utc_now(),
        "input": {"path": str(args.input.expanduser().resolve()), "sha256": sha256_file(args.input), "rows": len(rows)},
        "benchmark_cases_dir": str(args.cases_dir.expanduser().resolve()),
        "fixture_files_scanned": files,
        "fixture_rows_scanned": len(fixtures),
        "near_threshold": args.near_threshold,
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "rejection_reasons": reasons,
        "passed": bool(accepted),
        "critical_errors": [] if accepted else ["no rows remain after benchmark leakage protection"],
        "checks": {
            "all_benchmark_case_jsonl_scanned": bool(files),
            "canonical_prompt_scan_completed": True,
            "structural_signature_scan_completed": True,
            "near_similarity_scan_completed": True,
            "leak_scan_passed": bool(accepted),
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
        raise SystemExit(f"benchmark leakage check failed: {exc}")
