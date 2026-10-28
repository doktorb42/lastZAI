"""Build immutable, family-grouped data for the final focused PEva V4 pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import trening.agency_dataset_builder_v3 as v3
from trening.agency_corrective_dataset_v4 import EXPECTED_COUNTS, OUTPUT as V4_CORRECTIVE_DATASET
from trening.agency_dataset_policy import canonical, is_unsafe_unconfirmed_action_claim
from trening.agency_types import AgencyDecision


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "training" / "agency" / "pilot_v4"
TRAIN_PATH = OUTPUT_DIR / "peva_agency_pilot_v4_train.jsonl"
VALIDATION_PATH = OUTPUT_DIR / "peva_agency_pilot_v4_validation.jsonl"
MANIFEST_PATH = OUTPUT_DIR / "peva_agency_pilot_v4_split_manifest.json"
SEED = "peva-agency-pilot-v4-20261024"
FROZEN_BENCHMARK = ROOT / "benchmarks" / "peva_agency_v0.json"
PRIVATE_BENCHMARK = ROOT / "benchmarks" / "peva_private_v1.json"
V3_TOTAL = 264
EXPECTED_TOTAL = V3_TOTAL + sum(EXPECTED_COUNTS.values())


def _read(path: Path) -> list[dict[str, Any]]:
    return [dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _render(rows: list[dict[str, Any]]) -> bytes:
    return ("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)).encode("utf-8")


def source_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base, coverage = v3.source_rows()
    if len(base) != V3_TOTAL:
        raise RuntimeError(f"Expected immutable V3 corpus of {V3_TOTAL} rows, got {len(base)}")
    if not V4_CORRECTIVE_DATASET.is_file():
        raise RuntimeError(f"Missing immutable V4 corrective dataset: {V4_CORRECTIVE_DATASET}")
    corrections = _read(V4_CORRECTIVE_DATASET)
    if len(corrections) != sum(EXPECTED_COUNTS.values()):
        raise RuntimeError(f"V4 corrective dataset must contain {sum(EXPECTED_COUNTS.values())} rows, got {len(corrections)}")
    rows = base + corrections
    ids = [str(row.get("id") or "") for row in rows]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise RuntimeError("V4 source rows contain duplicate or empty IDs")
    coverage.update({"corrective_v4_count": len(corrections), "combined_count": len(rows)})
    return rows, coverage


def prepared_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from charakter.identity import load_peva_self

    raw, coverage = source_rows()
    peva = load_peva_self()
    prepared = [{
        "id": str(row["id"]),
        "messages": v3.v2._messages(row, peva=peva),
        "metadata": {
            "source": row["source"], "synthetic": bool(row["synthetic"]), "mode": row["mode"],
            "category_group": row["category_group"], "category": row["category"],
            "scenario_family": str(row.get("scenario_family") or row["category"]),
        },
    } for row in raw]
    return prepared, coverage


def split_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    families: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        families.setdefault(str(row["metadata"]["scenario_family"]), []).append(row)
    ordered = sorted(families.items(), key=lambda pair: hashlib.sha256(f"{SEED}:{pair[0]}".encode("utf-8")).hexdigest())
    target = round(len(rows) * 0.15)
    validation: list[dict[str, Any]] = []
    train: list[dict[str, Any]] = []
    for _family, members in ordered:
        (validation if len(validation) + len(members) <= target else train).extend(members)
    return sorted(train, key=lambda row: row["id"]), sorted(validation, key=lambda row: row["id"])


def _benchmark_inputs() -> set[str]:
    output: set[str] = set()
    for path in (FROZEN_BENCHMARK, PRIVATE_BENCHMARK):
        if path.is_file():
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document, list):
                output.update(canonical(item.get("input")) for item in document if isinstance(item, Mapping) and canonical(item.get("input")))
    return output


def _fingerprint(row: Mapping[str, Any]) -> str:
    target = row.get("target") if isinstance(row.get("target"), Mapping) else {}
    return "|".join((canonical(row.get("mode")), canonical(row.get("category")), canonical(row.get("input")), canonical(target.get("decision")), canonical(target.get("message"))))


def audit_rows(raw: list[dict[str, Any]], prepared: list[dict[str, Any]], train: list[dict[str, Any]], validation: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    fingerprints: set[str] = set()
    benchmark_inputs = _benchmark_inputs()
    decisions: Counter[str] = Counter()
    groups: Counter[str] = Counter()
    unsafe = 0
    for row in raw:
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
        decisions[decision] += 1
        groups[str(row.get("category_group") or "")] += 1
        try:
            if str(row.get("mode")) == "AGENCY_REACTIVE":
                AgencyDecision.parse_reactive(target)
            else:
                allowed = {str(item.get("uid") or "") for item in row.get("candidates", []) if isinstance(item, Mapping)}
                AgencyDecision.parse_proactive(target, allowed)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{row_id}:decision_message_contract:{exc}")
        if canonical(row.get("input")) in benchmark_inputs:
            errors.append(f"{row_id}:frozen_benchmark_input_overlap")
        if is_unsafe_unconfirmed_action_claim(target.get("message")):
            confirmed = " ".join(str(item) for item in row.get("context", [])).upper()
            if "CONFIRMED GAME FACT" not in confirmed:
                unsafe += 1
                errors.append(f"{row_id}:unsafe_action_claim")
    train_ids = {row["id"] for row in train}
    validation_ids = {row["id"] for row in validation}
    train_families = {row["metadata"]["scenario_family"] for row in train}
    validation_families = {row["metadata"]["scenario_family"] for row in validation}
    v4_distribution = {key: groups[key] for key in EXPECTED_COUNTS}
    if v4_distribution != EXPECTED_COUNTS:
        errors.append(f"v4_distribution:{v4_distribution}")
    social_positive = sum(
        1 for row in raw
        if row.get("category_group") == "corrective_v4_social_reciprocity"
        and row.get("target", {}).get("decision") in {"REPLY", "ASK_BACK", "CHANGE_TOPIC"}
    )
    gates = {
        "schema_and_contract_valid": not any(":decision_message_contract:" in item or ":target_not_object" in item for item in errors),
        "no_duplicate_fingerprints": not any(":duplicate_fingerprint" in item for item in errors),
        "no_train_validation_leakage": not (train_ids & validation_ids) and not (train_families & validation_families),
        "no_frozen_benchmark_overlap": not any(":frozen_benchmark_input_overlap" in item for item in errors),
        "unsafe_target_claims": unsafe,
        "v4_distribution": v4_distribution,
        "social_reply_examples": social_positive,
        "passed": not errors and len(raw) == EXPECTED_TOTAL and social_positive == 36,
    }
    return {"valid": not errors, "errors": errors, "gates": gates, "decision_counts": dict(sorted(decisions.items())), "group_counts": dict(sorted(groups.items()))}


def build(*, write: bool) -> dict[str, Any]:
    raw, coverage = source_rows()
    prepared, _ = prepared_rows()
    train, validation = split_rows(prepared)
    audit = audit_rows(raw, prepared, train, validation)
    report = {
        "schema_version": 1, "seed": SEED,
        "base_model": {"id": "Qwen/Qwen2.5-7B-Instruct", "revision": "a09a35458c702b33eeacc393d103063234e8bc28", "format": "transformers", "gguf_training_forbidden": True},
        "sources": {**coverage, "corrective_v4_sha256": hashlib.sha256(V4_CORRECTIVE_DATASET.read_bytes()).hexdigest(), "frozen_benchmark_sha256": hashlib.sha256(FROZEN_BENCHMARK.read_bytes()).hexdigest()},
        "split": {"train_count": len(train), "validation_count": len(validation), "family_grouped": True, "train_sha256": hashlib.sha256(_render(train)).hexdigest(), "validation_sha256": hashlib.sha256(_render(validation)).hexdigest()},
        "audit": audit, "pilot_gate": audit["gates"],
    }
    if write:
        if not audit["gates"]["passed"]:
            raise RuntimeError(f"V4 dataset gate failed: {audit['errors']}")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for path, content in ((TRAIN_PATH, _render(train)), (VALIDATION_PATH, _render(validation)), (MANIFEST_PATH, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))):
            if path.exists() and path.read_bytes() != content:
                raise RuntimeError(f"Refusing to overwrite immutable V4 artifact: {path}")
            if not path.exists():
                path.write_bytes(content)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or audit focused PEva V4 pilot data.")
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
    return 0 if report["pilot_gate"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
