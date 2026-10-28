"""Validate PEva Agency data without training or loading a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from trening.agency_dataset_policy import canonical, is_technical_text, is_unsafe_unconfirmed_action_claim


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DIR = ROOT / "data" / "training" / "agency" / "synthetic"
DEFAULT_DATASET = SYNTHETIC_DIR / "peva_agency_synthetic_v0.jsonl"
V1_DATASET = SYNTHETIC_DIR / "peva_agency_synthetic_v1.jsonl"
REAL_CANDIDATES = ROOT / "data" / "training" / "agency" / "real" / "peva_real_candidates_v0.jsonl"
COMBINED_MANIFEST = ROOT / "data" / "training" / "agency" / "manifests" / "peva_agency_combined_pilot_v0_manifest.json"
BENCHMARK = ROOT / "benchmarks" / "peva_private_v1.json"
V0_EXPECTED_COUNTS = {
    "natural_social": 28,
    "memory_continuity": 12,
    "personality_independence": 10,
    "ignore_wait_change_topic": 8,
    "action_safety": 10,
    "proactive_agency": 12,
}
V1_EXPECTED_COUNTS = {
    "natural_social": 20,
    "memory_continuity": 12,
    "personality_independence": 8,
    "ignore_wait_change_topic": 16,
    "action_safety": 12,
    "proactive_agency": 12,
}
# Backwards-compatible name for callers auditing the immutable v0 seed.
EXPECTED_COUNTS = V0_EXPECTED_COUNTS
REACTIVE_DECISIONS = frozenset({"REPLY", "IGNORE", "WAIT", "ASK_BACK", "CHANGE_TOPIC", "PROPOSE_GAME_ACTION"})
PROACTIVE_DECISIONS = frozenset({"START_CONVERSATION", "DO_NOTHING"})
REQUIRED_FIELDS = frozenset({"id", "source", "synthetic", "mode", "category_group", "category", "context", "player_memories", "input", "target"})


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return [], [f"utf8_read_error:{exc}"]
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line_{number}:invalid_json:{exc.msg}")
            continue
        if not isinstance(item, Mapping):
            errors.append(f"line_{number}:not_object")
            continue
        rows.append(dict(item))
    return rows, errors


def _benchmark_inputs() -> set[str]:
    if not BENCHMARK.is_file():
        return set()
    try:
        source = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {canonical(item.get("input")) for item in source if isinstance(item, Mapping) and canonical(item.get("input"))}


def _fingerprint(row: Mapping[str, Any]) -> str:
    target = row.get("target") if isinstance(row.get("target"), Mapping) else {}
    return "|".join((
        canonical(row.get("mode")), canonical(row.get("category")), canonical(row.get("input")),
        canonical(target.get("decision")), canonical(target.get("message")), canonical(target.get("target_uid")),
    ))


def _expected_counts(source: Path) -> Mapping[str, int]:
    return V1_EXPECTED_COUNTS if source.name.endswith("_v1.jsonl") else V0_EXPECTED_COUNTS


def audit_dataset(path: str | Path = DEFAULT_DATASET) -> dict[str, Any]:
    source = Path(path)
    rows, errors = _read_jsonl(source)
    ids: set[str] = set()
    fingerprints: set[str] = set()
    categories: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    benchmark = _benchmark_inputs()
    start_count = 0
    nothing_count = 0
    for index, row in enumerate(rows, start=1):
        label = f"row_{index}"
        missing = sorted(REQUIRED_FIELDS - set(row))
        if missing:
            errors.append(f"{label}:missing:{','.join(missing)}")
            continue
        row_id = str(row.get("id") or "")
        if not row_id or row_id in ids:
            errors.append(f"{label}:duplicate_or_empty_id")
        ids.add(row_id)
        fingerprint = _fingerprint(row)
        if fingerprint in fingerprints:
            errors.append(f"{label}:duplicate_record")
        fingerprints.add(fingerprint)
        if row.get("source") != "synthetic_curated" or row.get("synthetic") is not True:
            errors.append(f"{label}:synthetic_provenance")
        if not isinstance(row.get("context"), list) or not isinstance(row.get("player_memories"), list):
            errors.append(f"{label}:invalid_context_or_memories")
        group = str(row.get("category_group") or "")
        mode = str(row.get("mode") or "")
        categories[group] += 1
        modes[mode] += 1
        target = row.get("target")
        if not isinstance(target, Mapping):
            errors.append(f"{label}:invalid_target")
            continue
        decision = str(target.get("decision") or "")
        if mode == "AGENCY_REACTIVE":
            if decision not in REACTIVE_DECISIONS:
                errors.append(f"{label}:invalid_reactive_decision")
            if group == "proactive_agency":
                errors.append(f"{label}:proactive_group_with_reactive_mode")
            if row.get("input") is None:
                errors.append(f"{label}:reactive_input_missing")
        elif mode == "AGENCY_PROACTIVE":
            if decision not in PROACTIVE_DECISIONS:
                errors.append(f"{label}:invalid_proactive_decision")
            candidates = row.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                errors.append(f"{label}:proactive_candidates_missing")
            candidate_uids = {str(item.get("uid")) for item in candidates if isinstance(item, Mapping) and item.get("uid")}
            if decision == "START_CONVERSATION":
                start_count += 1
                if str(target.get("target_uid") or "") not in candidate_uids:
                    errors.append(f"{label}:invalid_proactive_target_uid")
            elif decision == "DO_NOTHING":
                nothing_count += 1
                if target.get("target_uid"):
                    errors.append(f"{label}:do_nothing_must_not_target_player")
            if row.get("input") is not None:
                errors.append(f"{label}:proactive_input_must_be_null")
        else:
            errors.append(f"{label}:invalid_mode")
        text = str(target.get("message") or "")
        if text and is_technical_text(text):
            errors.append(f"{label}:technical_output")
        if text and is_unsafe_unconfirmed_action_claim(text):
            errors.append(f"{label}:unsafe_action_claim")
        if canonical(row.get("input")) in benchmark:
            errors.append(f"{label}:benchmark_input_overlap")
    expected = _expected_counts(source)
    expected_total = sum(expected.values())
    if len(rows) != expected_total:
        errors.append(f"wrong_total:{len(rows)}")
    if dict(categories) != expected:
        errors.append(f"wrong_category_counts:{dict(sorted(categories.items()))}")
    if start_count < 5 or nothing_count < 5:
        errors.append(f"proactive_balance:start={start_count},do_nothing={nothing_count}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    return {
        "valid": not errors,
        "dataset": str(source.relative_to(ROOT)) if source.is_absolute() and source.is_relative_to(ROOT) else str(source),
        "sha256": digest,
        "count": len(rows),
        "category_counts": dict(sorted(categories.items())),
        "mode_counts": dict(sorted(modes.items())),
        "proactive_decisions": {"START_CONVERSATION": start_count, "DO_NOTHING": nothing_count},
        "errors": errors,
    }


def _rows_and_fingerprints(path: Path) -> tuple[list[dict[str, Any]], set[str], list[str]]:
    rows, errors = _read_jsonl(path)
    return rows, {_fingerprint(row) for row in rows}, errors


def audit_combined_pilot() -> dict[str, Any]:
    """Audit both immutable synthetic batches and their pilot-level gates."""
    v0 = audit_dataset(DEFAULT_DATASET)
    v1 = audit_dataset(V1_DATASET)
    v0_rows, v0_fingerprints, v0_errors = _rows_and_fingerprints(DEFAULT_DATASET)
    v1_rows, v1_fingerprints, v1_errors = _rows_and_fingerprints(V1_DATASET)
    errors = list(v0_errors) + list(v1_errors)
    errors.extend(f"v0:{item}" for item in v0["errors"])
    errors.extend(f"v1:{item}" for item in v1["errors"])
    ids = [str(row.get("id") or "") for row in v0_rows + v1_rows]
    if len(v0_rows) != 80 or len(v1_rows) != 80 or len(v0_rows) + len(v1_rows) != 160:
        errors.append("combined_synthetic_count_not_160")
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("combined_duplicate_or_empty_id")
    if v0_fingerprints & v1_fingerprints:
        errors.append("cross_file_duplicate_fingerprint")
    all_rows = v0_rows + v1_rows
    if any(row.get("source") != "synthetic_curated" or row.get("synthetic") is not True for row in all_rows):
        errors.append("synthetic_source_separation")
    control = sum(
        1 for row in all_rows
        if str((row.get("target") or {}).get("decision") or "") in {"IGNORE", "WAIT", "DO_NOTHING"}
    )
    action_safety = sum(1 for row in all_rows if str(row.get("category_group") or "") == "action_safety")
    reactive = sum(1 for row in all_rows if row.get("mode") == "AGENCY_REACTIVE")
    proactive = sum(1 for row in all_rows if row.get("mode") == "AGENCY_PROACTIVE")
    unsafe = sum(1 for row in all_rows if is_unsafe_unconfirmed_action_claim((row.get("target") or {}).get("message")))
    invalid_uid = 0
    for row in all_rows:
        if row.get("mode") != "AGENCY_PROACTIVE":
            continue
        target = row.get("target") or {}
        if target.get("decision") == "START_CONVERSATION":
            candidates = {str(item.get("uid") or "") for item in row.get("candidates", []) if isinstance(item, Mapping)}
            if str(target.get("target_uid") or "") not in candidates:
                invalid_uid += 1
    if control < 20:
        errors.append(f"control_decisions_below_20:{control}")
    if action_safety < 20:
        errors.append(f"action_safety_below_20:{action_safety}")
    if unsafe:
        errors.append(f"unsafe_action_claims:{unsafe}")
    if invalid_uid:
        errors.append(f"invalid_proactive_uids:{invalid_uid}")
    real_rows, real_errors = _read_jsonl(REAL_CANDIDATES)
    errors.extend(f"real:{item}" for item in real_errors)
    if any(row.get("synthetic") is not False for row in real_rows):
        errors.append("real_source_separation")
    real_positive = sum(1 for row in real_rows if bool(row.get("eligible_for_positive_training")) and str(row.get("feedback_status") or "").upper() in {"GOOD", "EDITED"})
    real_unreviewed = sum(1 for row in real_rows if str(row.get("feedback_status") or "UNREVIEWED").upper() not in {"GOOD", "EDITED"})
    pilot_gate = {
        "schema_valid": not errors,
        "no_train_validation_leakage": None,
        "agency_examples": len(all_rows),
        "reactive_examples": reactive,
        "proactive_examples": proactive,
        "ignore_wait_do_nothing": control,
        "action_safety_examples": action_safety,
        "unsafe_target_claims": unsafe,
        "invalid_proactive_uid": invalid_uid,
        "passed": not errors and len(all_rows) >= 120 and reactive >= 70 and proactive >= 20 and control >= 20 and action_safety >= 20 and unsafe == 0 and invalid_uid == 0,
    }
    return {
        "valid": not errors,
        "pilot_gate": pilot_gate,
        "v0": v0,
        "v1": v1,
        "combined_synthetic_count": len(all_rows),
        "real_reviewed_positive_count": real_positive,
        "real_unreviewed_count": real_unreviewed,
        "errors": errors,
    }


def write_combined_manifest(report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    report = dict(report or audit_combined_pilot())
    payload = {
        "schema_version": 1,
        "v0_synthetic_count": int(report["v0"]["count"]),
        "v1_synthetic_count": int(report["v1"]["count"]),
        "combined_synthetic_count": int(report["combined_synthetic_count"]),
        "real_reviewed_positive_count": int(report["real_reviewed_positive_count"]),
        "real_unreviewed_count": int(report["real_unreviewed_count"]),
        "v0_sha256": report["v0"]["sha256"],
        "v1_sha256": report["v1"]["sha256"],
        "pilot_audit_valid": bool(report["valid"]),
    }
    COMBINED_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if COMBINED_MANIFEST.exists() and COMBINED_MANIFEST.read_text(encoding="utf-8") != rendered:
        raise RuntimeError("Combined manifest exists with different immutable inputs; refusing overwrite.")
    if not COMBINED_MANIFEST.exists():
        COMBINED_MANIFEST.write_text(rendered, encoding="utf-8")
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit PEva Agency synthetic pilot data; never trains.")
    parser.add_argument("--dataset", help="Audit only one dataset (legacy-compatible).")
    parser.add_argument("--require-valid", action="store_true")
    parser.add_argument("--write-manifest", action="store_true", help="Write the combined immutable pilot manifest after a valid audit.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = audit_dataset(args.dataset) if args.dataset else audit_combined_pilot()
    if args.write_manifest:
        if not report["valid"]:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        report["manifest"] = write_combined_manifest(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] or not args.require_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
