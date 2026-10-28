"""Build immutable, family-grouped PEva Agency Pilot V3 data artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

import trening.agency_dataset_builder_v2 as v2
from trening.agency_corrective_dataset_v2 import EXPECTED_COUNTS as V2_CORRECTIVE_COUNTS
from trening.agency_corrective_dataset_v2 import OUTPUT as V2_CORRECTIVE_DATASET
from trening.agency_corrective_dataset_v3 import EXPECTED_COUNTS as V3_CORRECTIVE_COUNTS
from trening.agency_corrective_dataset_v3 import OUTPUT as V3_CORRECTIVE_DATASET
from trening.agency_dataset_policy import canonical, is_unsafe_unconfirmed_action_claim
from trening.agency_types import AgencyDecision


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "training" / "agency" / "pilot_v3"
TRAIN_PATH = OUTPUT_DIR / "peva_agency_pilot_v3_train.jsonl"
VALIDATION_PATH = OUTPUT_DIR / "peva_agency_pilot_v3_validation.jsonl"
MANIFEST_PATH = OUTPUT_DIR / "peva_agency_pilot_v3_split_manifest.json"
SEED = "peva-agency-pilot-v3-20261024"
FROZEN_BENCHMARK = ROOT / "benchmarks" / "peva_agency_v0.json"
PRIVATE_BENCHMARK = ROOT / "benchmarks" / "peva_private_v1.json"
EXPECTED_CORRECTIVE_COUNTS = dict(Counter(V2_CORRECTIVE_COUNTS) + Counter(V3_CORRECTIVE_COUNTS))


def _read(path: Path) -> list[dict[str, Any]]:
    return [dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _render(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return ("".join(_canonical_json(row) + "\n" for row in rows)).encode("utf-8")


def source_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reviewed, coverage = v2._reviewed_source_rows()
    required = (("V2", V2_CORRECTIVE_DATASET, 80), ("V3", V3_CORRECTIVE_DATASET, 30))
    corrections: list[dict[str, Any]] = []
    for name, path, expected in required:
        if not path.is_file():
            raise RuntimeError(f"Missing immutable {name} corrective dataset: {path}")
        rows = _read(path)
        if len(rows) != expected:
            raise RuntimeError(f"{name} corrective dataset must contain {expected} rows, got {len(rows)}")
        corrections.extend(rows)
    rows = reviewed + corrections
    ids = [str(row.get("id") or "") for row in rows]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise RuntimeError("V3 source rows contain duplicate or empty IDs")
    coverage.update({"corrective_v2_count": 80, "corrective_v3_count": 30, "combined_count": len(rows)})
    return rows, coverage


def prepared_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from charakter.identity import load_peva_self

    raw, coverage = source_rows()
    peva = load_peva_self()
    output: list[dict[str, Any]] = []
    for row in raw:
        output.append({
            "id": str(row["id"]),
            "messages": v2._messages(row, peva=peva),
            "metadata": {
                "source": row["source"], "synthetic": bool(row["synthetic"]), "mode": row["mode"],
                "category_group": row["category_group"], "category": row["category"],
                "scenario_family": str(row.get("scenario_family") or row["category"]),
            },
        })
    return output, coverage


def split_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        families.setdefault(str(row["metadata"]["scenario_family"]), []).append(row)
    ordered = sorted(families.items(), key=lambda pair: hashlib.sha256(f"{SEED}:{pair[0]}".encode("utf-8")).hexdigest())
    validation_target = round(len(rows) * 0.15)
    validation: list[dict[str, Any]] = []
    train: list[dict[str, Any]] = []
    for _family, members in ordered:
        (validation if len(validation) + len(members) <= validation_target else train).extend(members)
    return sorted(train, key=lambda row: row["id"]), sorted(validation, key=lambda row: row["id"])


def _benchmark_inputs() -> set[str]:
    values: set[str] = set()
    for path in (FROZEN_BENCHMARK, PRIVATE_BENCHMARK):
        if path.is_file():
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document, list):
                values.update(canonical(item.get("input")) for item in document if isinstance(item, Mapping) and canonical(item.get("input")))
    return values


def _fingerprint(row: Mapping[str, Any]) -> str:
    target = row.get("target") if isinstance(row.get("target"), Mapping) else {}
    return "|".join((canonical(row.get("mode")), canonical(row.get("category")), canonical(row.get("input")), canonical(target.get("decision")), canonical(target.get("message")), canonical(target.get("target_uid"))))


def audit_rows(raw_rows: list[dict[str, Any]], prepared: list[dict[str, Any]], train: list[dict[str, Any]], validation: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    fingerprints: set[str] = set()
    benchmark_inputs = _benchmark_inputs()
    decision_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    unsafe = invalid_uid = grounded_errors = 0
    for row in raw_rows:
        row_id = str(row.get("id") or "<missing>")
        fingerprint = _fingerprint(row)
        if fingerprint in fingerprints:
            errors.append(f"{row_id}:duplicate_fingerprint")
        fingerprints.add(fingerprint)
        target = row.get("target")
        if not isinstance(target, Mapping):
            errors.append(f"{row_id}:target_not_object")
            continue
        decision = str(target.get("decision") or "")
        decision_counts[decision] += 1
        group_counts[str(row.get("category_group") or "")] += 1
        mode = str(row.get("mode") or "")
        mode_counts[mode] += 1
        try:
            if mode == "AGENCY_REACTIVE":
                AgencyDecision.parse_reactive(target)
            elif mode == "AGENCY_PROACTIVE":
                allowed = {str(item.get("uid") or "") for item in row.get("candidates", []) if isinstance(item, Mapping)}
                AgencyDecision.parse_proactive(target, allowed)
            else:
                raise ValueError("unsupported mode")
        except ValueError as exc:
            errors.append(f"{row_id}:decision_message_contract:{exc}")
        if canonical(row.get("input")) in benchmark_inputs:
            errors.append(f"{row_id}:frozen_benchmark_input_overlap")
        message = canonical(target.get("message"))
        known = canonical(" ".join([str(row.get("input") or ""), *[str(item) for item in row.get("context") or []], *[str(item) for item in row.get("player_memories") or []]]))
        if any(trigger in message and trigger not in known for trigger in ("this morning", "last night", "at work today", "i slept", "i was at")):
            grounded_errors += 1
            errors.append(f"{row_id}:unsupported_situational_fact")
        confirmed = " ".join(str(item) for item in row.get("context", [])).upper()
        if is_unsafe_unconfirmed_action_claim(target.get("message")) and "CONFIRMED GAME FACT" not in confirmed:
            unsafe += 1
            errors.append(f"{row_id}:unsafe_action_claim")
        if mode == "AGENCY_PROACTIVE" and decision == "START_CONVERSATION":
            allowed = {str(item.get("uid") or "") for item in row.get("candidates", []) if isinstance(item, Mapping)}
            if str(target.get("target_uid") or "") not in allowed:
                invalid_uid += 1
                errors.append(f"{row_id}:invalid_target_uid")
    ids = {row["id"] for row in prepared}
    train_ids = {row["id"] for row in train}
    validation_ids = {row["id"] for row in validation}
    train_families = {row["metadata"]["scenario_family"] for row in train}
    validation_families = {row["metadata"]["scenario_family"] for row in validation}
    if len(ids) != len(prepared):
        errors.append("duplicate_prepared_id")
    if train_ids & validation_ids:
        errors.append("train_validation_id_leakage")
    if train_families & validation_families:
        errors.append("train_validation_family_leakage")
    corrective_distribution = {key: group_counts[key] for key in EXPECTED_CORRECTIVE_COUNTS}
    if corrective_distribution != EXPECTED_CORRECTIVE_COUNTS:
        errors.append(f"corrective_distribution:{corrective_distribution}")
    coverage = {
        "reactive": mode_counts["AGENCY_REACTIVE"], "proactive": mode_counts["AGENCY_PROACTIVE"],
        "message_bearing": sum(decision_counts[item] for item in ("REPLY", "ASK_BACK", "CHANGE_TOPIC", "PROPOSE_GAME_ACTION", "START_CONVERSATION")),
        "silent": sum(decision_counts[item] for item in ("IGNORE", "WAIT", "DO_NOTHING")),
        "social_reciprocity_cases": group_counts["corrective_social_reciprocity"],
        "social_context_cases": group_counts["corrective_social_context_grounding"],
        "social_boundary_cases": group_counts["corrective_social_boundary"],
        "social_silence_contrast_cases": group_counts["corrective_social_silence_contrast"],
        "context_grounding_lint_errors": grounded_errors,
    }
    gates = {
        "schema_and_contract_valid": not any(":decision_message_contract:" in item or ":target_not_object" in item for item in errors),
        "no_duplicate_fingerprints": not any(":duplicate_fingerprint" in item for item in errors),
        "no_train_validation_leakage": not (train_ids & validation_ids) and not (train_families & validation_families),
        "no_frozen_benchmark_overlap": not any(":frozen_benchmark_input_overlap" in item for item in errors),
        "unsafe_target_claims": unsafe, "invalid_proactive_uid": invalid_uid,
        "corrective_distribution": corrective_distribution, "decision_counts": dict(sorted(decision_counts.items())), "coverage": coverage,
        "passed": not errors and len(raw_rows) == 264 and decision_counts["DO_NOTHING"] >= 25 and decision_counts["WAIT"] >= 16 and decision_counts["ASK_BACK"] >= 12 and decision_counts["CHANGE_TOPIC"] >= 12 and coverage["social_reciprocity_cases"] == 14 and coverage["social_silence_contrast_cases"] == 6,
    }
    return {"valid": not errors, "errors": errors, "gates": gates, "group_counts": dict(sorted(group_counts.items())), "mode_counts": dict(sorted(mode_counts.items()))}


def build(*, write: bool) -> dict[str, Any]:
    raw, review_coverage = source_rows()
    prepared, _ = prepared_rows()
    train, validation = split_rows(prepared)
    audit = audit_rows(raw, prepared, train, validation)
    report = {
        "schema_version": 1, "seed": SEED,
        "base_model": {"id": "Qwen/Qwen2.5-7B-Instruct", "revision": "a09a35458c702b33eeacc393d103063234e8bc28", "format": "transformers", "gguf_training_forbidden": True},
        "review_coverage": review_coverage,
        "sources": {"reviewed_v0_v1_count": len(raw) - 110, "corrective_v2_count": 80, "corrective_v3_count": 30, "corrective_v2_sha256": hashlib.sha256(V2_CORRECTIVE_DATASET.read_bytes()).hexdigest(), "corrective_v3_sha256": hashlib.sha256(V3_CORRECTIVE_DATASET.read_bytes()).hexdigest(), "frozen_benchmark_sha256": hashlib.sha256(FROZEN_BENCHMARK.read_bytes()).hexdigest()},
        "split": {"train_count": len(train), "validation_count": len(validation), "family_grouped": True, "train_sha256": hashlib.sha256(_render(train)).hexdigest(), "validation_sha256": hashlib.sha256(_render(validation)).hexdigest()},
        "audit": audit, "pilot_gate": audit["gates"],
    }
    if write:
        if not audit["gates"]["passed"]:
            raise RuntimeError(f"V3 dataset gate failed: {audit['errors']}")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for path, content in ((TRAIN_PATH, _render(train)), (VALIDATION_PATH, _render(validation)), (MANIFEST_PATH, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))):
            if path.exists() and path.read_bytes() != content:
                raise RuntimeError(f"Refusing to overwrite immutable V3 artifact: {path}")
            if not path.exists():
                path.write_bytes(content)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or audit reviewed PEva Agency Pilot V3 data.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true")
    group.add_argument("--audit", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build(write=bool(args.build))
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["audit"]["gates"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
