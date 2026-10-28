#!/usr/bin/env python3
"""Optionally fetch the pinned llama.cpp source needed for HF-to-GGUF export.

No source is downloaded unless ``--execute`` is supplied.  The checkout stays
under ``lastz_model_benchmark/tools`` and is intentionally separate from the
already-installed Windows CUDA runtime binaries.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
BUNDLE_ROOT = TRAINING_ROOT.parent
DEFAULT_DESTINATION = BUNDLE_ROOT / "tools" / "llama.cpp-b11430"
REPOSITORY = "https://github.com/ggml-org/llama.cpp.git"
COMMIT = "8345f3339"
REPORTS = TRAINING_ROOT / "outputs" / "reports"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_within_bundle(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(BUNDLE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"destination must be inside {BUNDLE_ROOT}: {resolved}") from exc
    return resolved


def command(args: list[str], log: list[dict[str, Any]]) -> None:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    log.append({"command": args, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--execute", action="store_true", help="Actually clone the source repository.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS / f"llamacpp_source_{stamp}.json"
    report: dict[str, Any] = {"started_at": now(), "status": "planned", "commands": []}
    try:
        destination = ensure_within_bundle(args.destination)
        if destination.exists() and any(destination.iterdir()):
            raise RuntimeError(f"refusing to overwrite non-empty source destination: {destination}")
        plan = [
            ["git", "clone", "--filter=blob:none", "--no-checkout", REPOSITORY, str(destination)],
            ["git", "-C", str(destination), "checkout", COMMIT],
        ]
        report.update(destination=str(destination), repository=REPOSITORY, commit=COMMIT, plan=plan)
        if not args.execute:
            report.update(status="dry_run", finished_at=now())
            write_json(report_path, report)
            print(f"[OK] planned only: {report_path}")
            return 0
        command(plan[0], report["commands"])
        command(plan[1], report["commands"])
        expected = subprocess.run(
            ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True, capture_output=True, check=True
        ).stdout.strip()
        if not expected.startswith(COMMIT):
            raise RuntimeError(f"checked out unexpected commit: {expected}")
        report.update(status="passed", resolved_commit=expected, finished_at=now())
        write_json(report_path, report)
        print(f"[OK] llama.cpp source ready: {destination}")
        return 0
    except Exception as exc:
        report.update(
            status="failed",
            finished_at=now(),
            error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )
        write_json(report_path, report)
        print(f"[FAILED] {report['error']}\nReport: {report_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
