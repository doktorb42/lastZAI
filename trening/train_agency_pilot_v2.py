"""Guarded PEva Agency Pilot V2 QLoRA runner.

The training loop is intentionally shared with the V1 runner so the QLoRA
settings remain identical.  This module only replaces V1-specific data/run
bindings and adds an immutable frozen-benchmark hash gate.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from trening import train_agency_pilot_base as pilot
from trening.agency_corrective_dataset_v2 import OUTPUT as CORRECTIVE_DATASET
from trening.agency_dataset_builder_v2 import MANIFEST_PATH, SEED, TRAIN_PATH, VALIDATION_PATH, build


ROOT = Path(__file__).resolve().parents[1]
RUN_NAME = "peva-qwen2.5-7b-agency-pilot-v2"
FROZEN_BENCHMARK = ROOT / "benchmarks" / "peva_agency_v0.json"
FROZEN_BENCHMARK_SHA256 = "f11903411e2677d523e396c1e934965fa6f2da248a40aed7199b5ccf04157938"

# Rebind every V1-specific global before its functions are invoked.  The base
# module's functions resolve globals in its own module namespace at call time.
pilot.RUN_NAME = RUN_NAME
pilot.RUN_DIR = pilot.RUNS_DIR / RUN_NAME
pilot.MANIFEST_PATH = MANIFEST_PATH
pilot.SEED = SEED
pilot.TRAIN_PATH = TRAIN_PATH
pilot.VALIDATION_PATH = VALIDATION_PATH
pilot.build = build

_base_preflight = pilot.preflight
_base_save_evidence = pilot._save_evidence


def _frozen_benchmark_status() -> dict[str, str | bool]:
    actual = hashlib.sha256(FROZEN_BENCHMARK.read_bytes()).hexdigest() if FROZEN_BENCHMARK.is_file() else "missing"
    return {
        "path": str(FROZEN_BENCHMARK.relative_to(ROOT)),
        "expected_sha256": FROZEN_BENCHMARK_SHA256,
        "actual_sha256": actual,
        "matches_expected": actual == FROZEN_BENCHMARK_SHA256,
    }


def preflight() -> dict[str, Any]:
    report = _base_preflight()
    frozen = _frozen_benchmark_status()
    report["frozen_benchmark"] = frozen
    if not frozen["matches_expected"]:
        report.update(ready=False, reason="Frozen Agency benchmark hash does not match the pinned V2 evidence.")
    return report


def _save_evidence(preflight_report: Mapping[str, Any], run_dir: Path) -> None:
    _base_save_evidence(preflight_report, run_dir)
    sources = (
        ROOT / "trening" / "train_agency_pilot_v2.py",
        ROOT / "trening" / "agency_dataset_builder_v2.py",
        ROOT / "trening" / "agency_corrective_dataset_v2.py",
        ROOT / "trening" / "agency_review_studio.py",
        ROOT / "data" / "training" / "agency" / "review" / "peva_agency_manual_review.json",
        CORRECTIVE_DATASET,
        FROZEN_BENCHMARK,
    )
    for source in sources:
        if source.is_file():
            target = run_dir / "source_snapshot" / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


pilot.preflight = preflight
pilot._save_evidence = _save_evidence


def main(argv: list[str] | None = None) -> int:
    return pilot.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
