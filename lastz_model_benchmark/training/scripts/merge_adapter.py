#!/usr/bin/env python3
"""Merge a selected Action V1 LoRA adapter into its matching HF base model.

This is an explicit post-evaluation step.  It never touches a GGUF and refuses
to overwrite an existing merged directory.  CPU merge is intentional and is
not a training fallback; it keeps the merge independent of the 8 GB GPU.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
DEFAULT_CONFIG = TRAINING_ROOT / "configs" / "action_v1.yaml"
DEFAULT_OUTPUT_ROOT = TRAINING_ROOT / "outputs" / "merged"
REPORTS = TRAINING_ROOT / "outputs" / "reports"
EVALUATION_REPORT_GLOB = "checkpoint_evaluation_*.json"
ADAPTER_HASH_ALGORITHM = "sha256-directory-manifest-v1"
# Keep the full-precision merge base and all Hugging Face download metadata in
# the benchmark bundle.  This is intentionally separate from the reviewed
# 4-bit training checkpoint and avoids silently populating a user-level cache.
MODEL_CACHE_DIR = TRAINING_ROOT / "models" / "hf_cache"


class MergeError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MergeError(f"invalid JSON-compatible config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MergeError("config must be an object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_below(path: Path, parent: Path, label: str) -> Path:
    """Resolve *path* and reject symlink/path traversal outside *parent*."""

    resolved = path.resolve()
    root = parent.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MergeError(f"{label} must remain below {root}; got {resolved}") from exc
    return resolved


def resolve_scoped_path(value: Path, *, parent: Path, scope: Path, label: str) -> Path:
    """Resolve a CLI path without allowing it to escape the training bundle.

    Relative paths are intentionally relative to the relevant artifact root,
    rather than the caller's current directory.  This makes WSL and Windows
    invocations deterministic and prevents accidental project-root outputs.
    """

    requested = value if value.is_absolute() else parent / value
    return require_below(requested, scope, label)


def output_dir(args: argparse.Namespace, config: dict[str, Any]) -> Path:
    if args.output_dir:
        return resolve_scoped_path(
            args.output_dir,
            parent=DEFAULT_OUTPUT_ROOT,
            scope=DEFAULT_OUTPUT_ROOT,
            label="--output-dir",
        )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return require_below(
        DEFAULT_OUTPUT_ROOT / f"{config['experiment']}-merged-{stamp}",
        DEFAULT_OUTPUT_ROOT,
        "merged output",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_directory(path: Path, *, exclude_names: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Return the deterministic fingerprint also recorded by the evaluator.

    File contents, relative names, and ordering all affect the digest.  The
    implementation deliberately matches ``evaluate_checkpoint.py`` so an
    adapter changed after evaluation cannot be merged under the old report.
    ``exclude_names`` is used only for a manifest that fingerprints its own
    containing model directory (avoiding a self-referential hash).
    """

    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file() and item.name not in exclude_names)
    for item in files:
        # A file symlink could otherwise make a provenance fingerprint depend
        # on content outside the audited artifact directory.
        if item.is_symlink():
            raise MergeError(f"refusing symlinked artifact file: {item}")
        resolved_item = item.resolve()
        try:
            relative = resolved_item.relative_to(path).as_posix()
        except ValueError as exc:
            raise MergeError(f"artifact file escapes its directory: {item}") from exc
        file_digest = sha256_file(resolved_item)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return {
        "local_path": str(path),
        "hash_algorithm": ADAPTER_HASH_ALGORITHM,
        "sha256": digest.hexdigest(),
        "file_count": len(files),
    }


def strict_one(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 1.0


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MergeError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MergeError(f"{label} must be a JSON object: {path}")
    return value


def check_evaluation_report(
    report_path: Path,
    adapter: Path,
    adapter_provenance: dict[str, Any],
) -> dict[str, Any]:
    """Require a completed, full Action V1 holdout result for this adapter."""

    report = load_json(report_path, "checkpoint evaluation report")
    if report.get("status") != "passed":
        raise MergeError(f"evaluation report is not a completed pass: {report_path}")
    if report.get("limit") is not None:
        raise MergeError("evaluation report used --limit; a partial holdout cannot qualify an adapter")

    reported_adapter = report.get("adapter")
    if not isinstance(reported_adapter, str) or not reported_adapter:
        raise MergeError("evaluation report does not record its adapter path")
    try:
        reported_adapter_path = require_below(Path(reported_adapter), TRAINING_ROOT, "evaluation report adapter")
    except (OSError, ValueError) as exc:
        raise MergeError("evaluation report contains an invalid local adapter path") from exc
    if reported_adapter_path != adapter:
        raise MergeError(
            "evaluation report belongs to a different adapter: "
            f"{reported_adapter_path} != {adapter}"
        )

    report_provenance = report.get("adapter_provenance")
    if not isinstance(report_provenance, dict):
        raise MergeError("evaluation report lacks adapter_provenance; rerun the real checkpoint evaluation")
    if report_provenance.get("hash_algorithm") != ADAPTER_HASH_ALGORITHM:
        raise MergeError("evaluation report uses an unsupported adapter hash algorithm")
    if report_provenance.get("local_path") != str(adapter):
        raise MergeError("evaluation report adapter_provenance.local_path does not match the requested adapter")
    if not is_sha256(report_provenance.get("sha256")):
        raise MergeError("evaluation report has an invalid adapter SHA-256")
    if not isinstance(report_provenance.get("file_count"), int) or isinstance(report_provenance["file_count"], bool):
        raise MergeError("evaluation report has an invalid adapter file count")
    if report_provenance.get("sha256") != adapter_provenance["sha256"]:
        raise MergeError("adapter contents changed after holdout evaluation; rerun evaluation before merge")
    if report_provenance.get("file_count") != adapter_provenance["file_count"]:
        raise MergeError("adapter file set changed after holdout evaluation; rerun evaluation before merge")

    suites = report.get("suites")
    if not isinstance(suites, dict):
        raise MergeError("evaluation report lacks action suite results")
    production = suites.get("production_action_suite")
    metrics = production.get("metrics") if isinstance(production, dict) else None
    if not isinstance(metrics, dict):
        raise MergeError("evaluation report lacks production_action_suite metrics")
    if not isinstance(metrics.get("count"), int) or isinstance(metrics["count"], bool) or metrics["count"] < 1:
        raise MergeError("evaluation report has no production Action V1 samples")
    for name in ("schema_rate", "legal_rate", "exact_rate"):
        if not strict_one(metrics.get(name)):
            raise MergeError(f"production Action V1 {name} is not exactly 100%")

    production_gate = report.get("production_gate")
    if not isinstance(production_gate, dict) or production_gate.get("passed") is not True:
        raise MergeError("evaluation report's production gate did not pass")
    for name in ("schema_valid_100", "legal_100", "exact_100"):
        if production_gate.get(name) is not True:
            raise MergeError(f"evaluation report production gate is missing {name}=true")

    return {
        "path": str(report_path),
        "sha256": sha256_file(report_path),
        "adapter_provenance": report_provenance,
        "production_metrics": {
            "count": metrics["count"],
            "schema_rate": metrics["schema_rate"],
            "legal_rate": metrics["legal_rate"],
            "exact_rate": metrics["exact_rate"],
        },
        "production_gate": {
            "schema_valid_100": production_gate["schema_valid_100"],
            "legal_100": production_gate["legal_100"],
            "exact_100": production_gate["exact_100"],
            "passed": production_gate["passed"],
        },
    }


def locate_qualifying_evaluation(adapter: Path, adapter_provenance: dict[str, Any]) -> dict[str, Any]:
    """Find the newest qualifying report for this exact immutable adapter."""

    candidates = sorted(
        (require_below(path, REPORTS, "checkpoint evaluation report") for path in REPORTS.glob(EVALUATION_REPORT_GLOB)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    failures: list[str] = []
    for candidate in candidates:
        try:
            return check_evaluation_report(candidate, adapter, adapter_provenance)
        except MergeError as exc:
            failures.append(f"{candidate.name}: {exc}")
    detail = "; ".join(failures[:3]) or "no checkpoint_evaluation_*.json reports found"
    raise MergeError(
        "no qualifying Action V1 checkpoint evaluation was found for this adapter. "
        "Run evaluate_checkpoint.py without --limit after training. "
        f"Closest report diagnostics: {detail}"
    )


def preflight(adapter: Path, destination: Path) -> None:
    if not adapter.is_dir():
        raise MergeError(f"adapter directory not found: {adapter}")
    if not (adapter / "adapter_config.json").is_file():
        raise MergeError(f"not a PEFT adapter directory (missing adapter_config.json): {adapter}")
    if destination.exists() and any(destination.iterdir()):
        raise MergeError(f"refusing to overwrite merged model directory: {destination}")
    free = shutil.disk_usage(destination.parent if destination.parent.exists() else TRAINING_ROOT).free
    # The fp16 source is roughly 6.2 GB; saving sharded safetensors needs a
    # healthy margin for download cache, temporary files, and report artifacts.
    if free < 18 * 1024**3:
        raise MergeError("less than 18 GiB free disk space; merge is intentionally blocked")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True, help="Accepted LoRA adapter directory.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--evaluation-report",
        type=Path,
        help="Completed checkpoint_evaluation_*.json for this exact adapter. Defaults to newest qualifying local report.",
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report: dict[str, Any] = {"started_at": now(), "status": "running", "adapter": str(args.adapter)}
    report_path = REPORTS / f"merge_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
    try:
        config_path = resolve_scoped_path(args.config, parent=TRAINING_ROOT, scope=TRAINING_ROOT, label="--config")
        config = load_config(config_path)
        destination = output_dir(args, config)
        adapter = resolve_scoped_path(args.adapter, parent=TRAINING_ROOT, scope=TRAINING_ROOT, label="--adapter")
        preflight(adapter, destination)
        adapter_provenance = fingerprint_directory(adapter)
        if args.evaluation_report:
            evaluation_path = resolve_scoped_path(
                args.evaluation_report,
                parent=REPORTS,
                scope=REPORTS,
                label="--evaluation-report",
            )
            if not evaluation_path.is_file():
                raise MergeError(f"checkpoint evaluation report not found: {evaluation_path}")
            evaluation = check_evaluation_report(evaluation_path, adapter, adapter_provenance)
        else:
            evaluation = locate_qualifying_evaluation(adapter, adapter_provenance)
        model = config.get("model", {})
        report.update(
            config=str(config_path),
            config_sha256=sha256_file(config_path),
            adapter=str(adapter),
            adapter_provenance=adapter_provenance,
            holdout_evaluation=evaluation,
            merge_model_id=model.get("merge_model_id"),
            merge_revision=model.get("merge_revision"),
            model_cache_dir=str(MODEL_CACHE_DIR),
            output_dir=str(destination),
            device=args.device,
        )
        if args.dry_run:
            report.update(status="dry_run", finished_at=now())
            write_json(report_path, report)
            print(f"[OK] dry run: {report_path}")
            return 0

        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if args.device == "cuda" and not torch.cuda.is_available():
            raise MergeError("CUDA merge requested but CUDA is unavailable; choose --device cpu explicitly")
        destination.mkdir(parents=True, exist_ok=False)
        device_map: dict[str, Any] = {"": 0} if args.device == "cuda" else {"": "cpu"}
        dtype = torch.float16
        started = time.perf_counter()
        tokenizer = AutoTokenizer.from_pretrained(
            model["merge_model_id"],
            revision=model["merge_revision"],
            trust_remote_code=False,
            cache_dir=str(MODEL_CACHE_DIR),
        )
        base = AutoModelForCausalLM.from_pretrained(
            model["merge_model_id"],
            revision=model["merge_revision"],
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
            device_map=device_map,
            trust_remote_code=False,
            cache_dir=str(MODEL_CACHE_DIR),
        )
        combined = PeftModel.from_pretrained(base, str(adapter))
        merged = combined.merge_and_unload(safe_merge=True)
        merged.save_pretrained(destination, safe_serialization=True, max_shard_size="2GB")
        tokenizer.save_pretrained(destination)
        manifest = {
            "schema_version": 1,
            "artifact_type": "lastz_merged_hf_action_model",
            "status": "passed",
            "created_at": now(),
            "experiment": config.get("experiment"),
            "adapter": str(adapter),
            "adapter_provenance": adapter_provenance,
            "holdout_evaluation": evaluation,
            "base_model": model["merge_model_id"],
            "base_revision": model["merge_revision"],
            "training_source": model["training_model_id"],
            "training_revision": model["training_revision"],
            "merge_device": args.device,
            "contains_chat_template": bool(getattr(tokenizer, "chat_template", None)),
            "format": "safetensors",
        }
        manifest["merged_model_provenance"] = fingerprint_directory(
            destination, exclude_names=frozenset({"lastz_merge_manifest.json"})
        )
        write_json(destination / "lastz_merge_manifest.json", manifest)
        report.update(
            status="passed",
            finished_at=now(),
            elapsed_seconds=round(time.perf_counter() - started, 3),
            manifest=manifest,
        )
        write_json(report_path, report)
        print(f"[OK] merged HF model: {destination}")
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
