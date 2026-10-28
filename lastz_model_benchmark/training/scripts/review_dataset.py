#!/usr/bin/env python3
"""Human-only review queue for future-gated Last Z Chat V1 candidates.

Approved rows are still *not* training data.  They remain inside
``training/data/reviewed`` with an explicit pending Action V1 gate; rejected
rows are written to ``training/data/rejected``.  The script never reads a
benchmark fixture, writes ``data/processed``, calls a model, or interacts with
the game, ADB, or BlueStacks.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from chat_quality import (
    combined_warnings,
    copy_with_candidate,
    display_value,
    extract_candidate_text,
    extract_chat_input,
    extract_expected,
    extract_scenario,
    extract_source,
    validate_player_ready_output,
)
from dataset_common import DATA_ROOT, BUNDLE_ROOT, DatasetError, read_jsonl, safe_within_training, write_jsonl


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
REVIEWED_DIR = DATA_ROOT / "reviewed"
REJECTED_DIR = DATA_ROOT / "rejected"


class ReviewError(RuntimeError):
    """An interactive review precondition was not met."""


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_input(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if _is_within(resolved, BUNDLE_ROOT / "benchmarks"):
        raise ReviewError("benchmark fixtures are protected and cannot enter the Chat V1 review queue")
    if not resolved.is_file():
        raise ReviewError(f"review input JSONL not found: {resolved}")
    return resolved


def _resolve_output(value: str | None, root: Path, default_name: str) -> Path:
    requested = Path(value) if value else root / default_name
    candidate = requested if requested.is_absolute() else TRAINING_ROOT / requested
    candidate = safe_within_training(candidate)
    if not _is_within(candidate, root):
        raise ReviewError(f"output must stay under {root}")
    if candidate.suffix.casefold() != ".jsonl":
        raise ReviewError("output must have a .jsonl extension")
    return candidate


def _print_section(label: str, value: Any) -> None:
    print(f"\n{label}:")
    print(display_value(value))


def _show_record(record: Mapping[str, Any], position: int, total: int) -> None:
    print("\n" + "=" * 78)
    print(f"Chat V1 review record {position}/{total}")
    _print_section("INPUT", extract_chat_input(record))
    _print_section("EXPECTED", extract_expected(record))
    _print_section("SOURCE", extract_source(record))
    _print_section("SCENARIO", extract_scenario(record))
    candidate = extract_candidate_text(record)
    _print_section("CANDIDATE", candidate)
    warnings = combined_warnings(record)
    _print_section("WARNINGS", warnings or "<none>")


def _prompt_choice() -> str:
    valid = {"a": "approve", "r": "reject", "e": "edit", "s": "skip", "q": "quit"}
    while True:
        raw = input("Decision [a]pprove / [r]eject / [e]dit / [s]kip / [q]uit: ").strip().casefold()
        if raw in valid:
            return valid[raw]
        print("Please enter a, r, e, s, or q.")


def _prompt_optional_reason() -> str | None:
    value = input("Optional rejection reason (Enter for none): ").strip()
    return value or None


def _edit_candidate(record: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    print("Enter a replacement player-ready reply. Blank input cancels the edit.")
    replacement = input("> ")
    if not replacement.strip():
        return None, None
    verdict = validate_player_ready_output(replacement, record, apostrophe_policy="normalize")
    if not verdict["valid"]:
        print("Edit is not eligible for approval:")
        for reason in verdict["errors"]:
            print(f"- {reason}")
        return None, verdict
    return copy_with_candidate(record, verdict["normalized_text"]), verdict


def _review_metadata(decision: str, original: Mapping[str, Any], verdict: Mapping[str, Any], reason: str | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "decision": decision,
        "reviewed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "quality_at_review": copy.deepcopy(dict(verdict)),
        "original_candidate_present": extract_candidate_text(original) is not None,
    }
    if reason:
        metadata["reason"] = reason
    return metadata


def _approved_record(record: Mapping[str, Any], original: Mapping[str, Any], verdict: Mapping[str, Any]) -> dict[str, Any]:
    approved = copy.deepcopy(dict(record))
    approved["review_status"] = "approved_human_review_pending_action_v1_gate"
    approved["training_eligibility"] = "not_training_ready"
    approved["chat_v1_gate"] = "action_v1_must_pass_before_chat_training"
    approved["human_review"] = _review_metadata("approved", original, verdict)
    return approved


def _rejected_record(record: Mapping[str, Any], verdict: Mapping[str, Any], reason: str | None) -> dict[str, Any]:
    rejected = copy.deepcopy(dict(record))
    rejected["review_status"] = "rejected_human_review"
    rejected["training_eligibility"] = "not_training_ready"
    rejected["human_review"] = _review_metadata("rejected", record, verdict, reason)
    reasons = list(verdict.get("errors", []))
    if reason:
        reasons.append(reason)
    rejected["rejection_reasons"] = list(dict.fromkeys(str(item) for item in reasons)) or ["human_rejected"]
    return rejected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interactively approve, reject, edit, or skip future-gated Last Z Chat V1 candidates."
    )
    parser.add_argument("--input", required=True, help="Candidate JSONL. Benchmark fixture paths are refused.")
    parser.add_argument("--approved-output", help="Restricted to training/data/reviewed/*.jsonl.")
    parser.add_argument("--rejected-output", help="Restricted to training/data/rejected/*.jsonl.")
    parser.add_argument("--limit", type=int, help="Review at most this many source records.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        raise ReviewError("--limit must be positive")
    input_path = _safe_input(Path(args.input))
    records = read_jsonl(input_path)
    if args.limit is not None:
        records = records[: args.limit]
    stamp = utc_stamp()
    approved_output = _resolve_output(args.approved_output, REVIEWED_DIR, f"chat_reviewed_{stamp}.jsonl")
    rejected_output = _resolve_output(args.rejected_output, REJECTED_DIR, f"chat_review_rejected_{stamp}.jsonl")
    if approved_output == rejected_output or approved_output == input_path or rejected_output == input_path:
        raise ReviewError("input, approved output, and rejected output must be distinct paths")

    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped = 0
    edited = 0
    for position, original in enumerate(records, 1):
        current: dict[str, Any] = copy.deepcopy(original)
        while True:
            _show_record(current, position, len(records))
            decision = _prompt_choice()
            candidate = extract_candidate_text(current)
            verdict = validate_player_ready_output(candidate, current, apostrophe_policy="normalize")
            if decision == "quit":
                print("Stopping review; completed decisions will be saved.")
                records = records[:position]
                break
            if decision == "skip":
                skipped += 1
                break
            if decision == "edit":
                replacement, edit_verdict = _edit_candidate(current)
                if replacement is not None and edit_verdict is not None:
                    current = replacement
                    edited += 1
                    print("Edit accepted for review. Inspect it and choose approve/reject/skip.")
                continue
            if decision == "approve":
                if not verdict["valid"]:
                    print("Cannot approve this candidate until it passes the hard quality gate. Edit, reject, or skip it.")
                    for reason in verdict["errors"]:
                        print(f"- {reason}")
                    continue
                approved.append(_approved_record(current, original, verdict))
                break
            if decision == "reject":
                rejected.append(_rejected_record(current, verdict, _prompt_optional_reason()))
                break
        else:  # pragma: no cover - the loop always exits through a decision
            continue
        if decision == "quit":
            break

    if approved:
        write_jsonl(approved_output, approved)
    if rejected:
        write_jsonl(rejected_output, rejected)
    print(
        json.dumps(
            {
                "status": "review_complete",
                "approved": len(approved),
                "rejected": len(rejected),
                "skipped": skipped,
                "edited": edited,
                "approved_output": str(approved_output) if approved else None,
                "rejected_output": str(rejected_output) if rejected else None,
                "training_status": "No reviewed row is training-ready; Action V1 must pass first.",
                "safety": [
                    "no_processed_data_written",
                    "no_benchmark_fixture_read_or_written",
                    "no_teacher_or_game_call",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DatasetError, ReviewError, ValueError, EOFError, KeyboardInterrupt) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
