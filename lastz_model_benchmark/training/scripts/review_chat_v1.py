#!/usr/bin/env python3
"""Human review workflow for canonical PEva Chat V1 source records.

The reviewer sees the immutable V2 context, the incoming player message, any
model draft, and the independently preserved approved reply.  Approval never
overwrites the original draft.  This script only writes review records; it
does not create a training split or call a model/game client.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from chat_quality import validate_player_ready_output
from chat_v1_contract import (
    CATEGORIES,
    LENGTH_BANDS,
    ChatV1ContractError,
    display_context,
    incoming_message,
    reply_word_count,
    utc_now,
    validate_source_record,
)
from dataset_common import BUNDLE_ROOT, DATA_ROOT, DatasetError, read_jsonl, safe_within_training, write_jsonl


REVIEWED_DIR = DATA_ROOT / "reviewed"
REJECTED_DIR = DATA_ROOT / "rejected"


class ReviewError(RuntimeError):
    """An interactive review operation was invalid or unsafe."""


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_input(value: Path) -> Path:
    path = value.expanduser().resolve()
    if _within(path, BUNDLE_ROOT / "benchmarks"):
        raise ReviewError("benchmark fixtures are protected and cannot be reviewed as Chat V1 source data")
    if _within(path, DATA_ROOT / "processed"):
        raise ReviewError("processed training splits cannot re-enter human review")
    if not path.is_file():
        raise ReviewError(f"review input JSONL not found: {path}")
    return path


def _output(value: Path, root: Path, label: str) -> Path:
    path = value if value.is_absolute() else root.parent.parent / value
    path = safe_within_training(path)
    if not _within(path, root) or path.suffix.casefold() != ".jsonl":
        raise ReviewError(f"{label} must be a JSONL path under {root}")
    return path


def _short_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _show(record: Mapping[str, Any], position: int, total: int) -> None:
    print("\n" + "=" * 88)
    print(f"PEva Chat V1 review record {position}/{total}: {record.get('record_id', '<invalid>')}")
    print(f"source_type={record.get('source_type')} category={record.get('category')} approval={record.get('approval', {}).get('status') if isinstance(record.get('approval'), Mapping) else '<invalid>'}")
    print("\nINCOMING PLAYER MESSAGE:\n" + (incoming_message(record) or "<missing>"))
    print("\nMODEL DRAFT (preserved; never overwritten):\n" + (str(record.get("model_draft")) if record.get("model_draft") is not None else "<none>"))
    print("\nCURRENT APPROVED REPLY:\n" + (str(record.get("approved_reply")) if record.get("approved_reply") is not None else "<none>"))
    print("\nGROUPS (metadata only; never model-visible):\n" + _short_json(record.get("grouping")))
    print("\nCANONICAL TASK_CHAT V2 INPUT (immutable):\n" + display_context(record))


def _read_choice() -> str:
    choices = {"a", "e", "r", "s", "q"}
    while True:
        choice = input("Decision [a]pprove / [e]dit+approve / [r]eject / [s]kip / [q]uit: ").strip().casefold()
        if choice in choices:
            return choice
        print("Enter a, e, r, s, or q.")


def _category(current: Any) -> str | None:
    if current in CATEGORIES:
        prompt = f"Category [{current}] (Enter to keep; options: {', '.join(sorted(CATEGORIES))}): "
    else:
        prompt = "Category (required; options: " + ", ".join(sorted(CATEGORIES)) + "): "
    while True:
        value = input(prompt).strip()
        if not value and current in CATEGORIES:
            return str(current)
        if value in CATEGORIES:
            return value
        print("A valid category is required for approval.")


def _length_band(current: Any, reply: str) -> str:
    """Require an explicit, truthful reply-length label during approval."""

    words = reply_word_count(reply)
    fitting = [name for name, (minimum, maximum) in LENGTH_BANDS.items() if minimum <= words <= maximum]
    prompt_options = ", ".join(f"{name}={minimum}-{maximum}" for name, (minimum, maximum) in LENGTH_BANDS.items())
    default = str(current) if current in fitting else (fitting[0] if len(fitting) == 1 else None)
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"Length band for {words} words{suffix} ({prompt_options}): ").strip()
        chosen = default if not value and default else value
        if chosen in fitting:
            return str(chosen)
        if chosen in LENGTH_BANDS:
            minimum, maximum = LENGTH_BANDS[str(chosen)]
            print(f"{chosen} requires {minimum}-{maximum} words; this reply has {words}.")
        else:
            print("Choose a valid band that fits the approved reply.")


def _reply_candidate(record: Mapping[str, Any]) -> str | None:
    approved = record.get("approved_reply")
    if isinstance(approved, str):
        return approved
    draft = record.get("model_draft")
    return draft if isinstance(draft, str) else None


def _validate_reply(reply: str, record: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
    snapshot = record.get("task_snapshot")
    task_input = snapshot.get("input") if isinstance(snapshot, Mapping) else None
    if not isinstance(task_input, Mapping):
        return None, {"valid": False, "errors": ["task_snapshot.input missing"]}
    verdict = dict(
        validate_player_ready_output(
            reply,
            {"input": task_input.get("context"), "grounding": task_input.get("context")},
            apostrophe_policy="normalize",
        )
    )
    return str(verdict.get("normalized_text")) if verdict.get("valid") else None, verdict


def _approved(record: Mapping[str, Any], *, reply: str, reviewer: str, category: str, length_band: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    result["approved_reply"] = reply
    result["category"] = category
    result["length_band"] = length_band
    result["approval"] = {
        "status": "approved",
        "reviewer": reviewer,
        "reviewed_at": utc_now(),
        "reason": "human_approved_chat_v1_review",
    }
    return result


def _rejected(record: Mapping[str, Any], *, reviewer: str, reason: str | None) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    result["approved_reply"] = None
    result["approval"] = {
        "status": "rejected",
        "reviewer": reviewer,
        "reviewed_at": utc_now(),
        "reason": reason or "human_rejected_chat_v1_review",
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--reviewer", required=True, help="Short operator alias, never a UID/room/database ID.")
    parser.add_argument("--approved-output", type=Path, default=REVIEWED_DIR / "chat_v1_approved_reviewed.jsonl")
    parser.add_argument("--rejected-output", type=Path, default=REJECTED_DIR / "chat_v1_human_rejected.jsonl")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ReviewError("--limit must be positive")
    input_path = _safe_input(args.input)
    approved_path = _output(args.approved_output, REVIEWED_DIR, "approved output")
    rejected_path = _output(args.rejected_output, REJECTED_DIR, "rejected output")
    if len({input_path, approved_path, rejected_path}) != 3:
        raise ReviewError("input, approved output, and rejected output must be distinct")
    rows = read_jsonl(input_path)
    if args.limit is not None:
        rows = rows[: args.limit]
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped = 0
    invalid = 0
    for position, raw in enumerate(rows, 1):
        normalized, errors, _details = validate_source_record(raw, require_approved=False)
        if normalized is None or errors:
            invalid += 1
            print("\n" + "=" * 88)
            print(f"Skipping malformed source record {position}/{len(rows)}: {'; '.join(errors)}")
            continue
        current = normalized
        _show(current, position, len(rows))
        decision = _read_choice()
        if decision == "q":
            break
        if decision == "s":
            skipped += 1
            continue
        if decision == "r":
            reason = input("Optional rejection reason: ").strip() or None
            rejected.append(_rejected(current, reviewer=args.reviewer, reason=reason))
            continue
        candidate = _reply_candidate(current)
        if decision == "e":
            candidate = input("Replacement player-ready reply (blank cancels): ").strip()
            if not candidate:
                print("Edit cancelled; record skipped.")
                skipped += 1
                continue
        if candidate is None:
            print("No draft/reply exists. Edit a reply before approving.")
            skipped += 1
            continue
        safe_reply, verdict = _validate_reply(candidate, current)
        if safe_reply is None:
            print("Reply cannot be approved. Quality gate errors:")
            for error in verdict.get("errors", []):
                print(f"- {error}")
            skipped += 1
            continue
        category = _category(current.get("category"))
        assert category is not None
        length_band = _length_band(current.get("length_band"), safe_reply)
        candidate_record = _approved(
            current,
            reply=safe_reply,
            reviewer=args.reviewer,
            category=category,
            length_band=length_band,
        )
        final, final_errors, _details = validate_source_record(candidate_record, require_approved=True)
        if final is None or final_errors:
            print("Approval is blocked by the strict source contract:")
            for error in final_errors:
                print(f"- {error}")
            skipped += 1
            continue
        approved.append(final)
    if approved:
        write_jsonl(approved_path, approved)
    if rejected:
        write_jsonl(rejected_path, rejected)
    print(
        json.dumps(
            {
                "status": "review_complete",
                "approved": len(approved),
                "rejected": len(rejected),
                "skipped": skipped,
                "invalid": invalid,
                "approved_output": str(approved_path) if approved else None,
                "rejected_output": str(rejected_path) if rejected else None,
                "source_type_counts_approved": dict(sorted(Counter(str(row["source_type"]) for row in approved).items())),
                "safety": [
                    "original_model_draft_preserved_separately",
                    "only_human_approved_reply_can_reach_validation",
                    "no_training_started",
                    "no_game_adb_or_bluestacks_interaction",
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
    except (DatasetError, ReviewError, ChatV1ContractError, EOFError, KeyboardInterrupt, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

