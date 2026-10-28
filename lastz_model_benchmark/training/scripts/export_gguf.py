#!/usr/bin/env python3
"""Convert a merged Last Z HF model to F16 GGUF and quantize it to Q6_K.

The only supported direction is merged Hugging Face weights -> F16 GGUF ->
Q6_K.  This blocks accidental Q4-to-Q6 requantization, which cannot recover
lost precision.  It performs no inference and never starts the game.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
BUNDLE_ROOT = TRAINING_ROOT.parent
DEFAULT_OUT = TRAINING_ROOT / "outputs" / "gguf"
DEFAULT_SOURCE = BUNDLE_ROOT / "tools" / "llama.cpp-b11430"
REPORTS = TRAINING_ROOT / "outputs" / "reports"
EXPECTED_SOURCE_COMMIT = "8345f3339"
MERGED_ROOT = TRAINING_ROOT / "outputs" / "merged"
MERGE_MANIFEST_NAME = "lastz_merge_manifest.json"
GGUF_MANIFEST_NAME = "lastz_gguf_export_manifest.json"
DIRECTORY_HASH_ALGORITHM = "sha256-directory-manifest-v1"


class ExportError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_below(path: Path, parent: Path, label: str) -> Path:
    """Resolve a path and refuse project-root/external artifacts."""

    resolved = path.resolve()
    root = parent.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ExportError(f"{label} must remain below {root}; got {resolved}") from exc
    return resolved


def resolve_scoped_path(value: Path, *, parent: Path, scope: Path, label: str) -> Path:
    requested = value if value.is_absolute() else parent / value
    return require_below(requested, scope, label)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_directory(path: Path, *, exclude_names: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Fingerprint an artifact directory without hashing its own manifest."""

    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file() and item.name not in exclude_names)
    for item in files:
        if item.is_symlink():
            raise ExportError(f"refusing symlinked artifact file: {item}")
        resolved_item = item.resolve()
        try:
            relative = resolved_item.relative_to(path).as_posix()
        except ValueError as exc:
            raise ExportError(f"artifact file escapes its model directory: {item}") from exc
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(resolved_item).encode("ascii"))
        digest.update(b"\n")
    return {
        "local_path": str(path),
        "hash_algorithm": DIRECTORY_HASH_ALGORITHM,
        "sha256": digest.hexdigest(),
        "file_count": len(files),
    }


def strict_one(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 1.0


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"invalid merge manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"merge manifest must be a JSON object: {path}")
    return value


def discover_quantizer() -> Path | None:
    candidates = sorted((BUNDLE_ROOT / "runtime").glob("llama-cpp-cuda-*/llama-quantize.exe"), reverse=True)
    if candidates:
        return candidates[0]
    candidates = sorted((BUNDLE_ROOT / "runtime").glob("llama-cpp-*/llama-quantize.exe"), reverse=True)
    if candidates:
        return candidates[0]
    local = shutil.which("llama-quantize") or shutil.which("llama-quantize.exe")
    return Path(local) if local else None


def run_command(command: list[str], log: Path, *, dry_run: bool) -> None:
    with log.open("a", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(command) + "\n")
        if dry_run:
            stream.write("[dry-run]\n")
            return
        process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, text=True, check=False)
        if process.returncode != 0:
            raise ExportError(f"command failed with exit code {process.returncode}; see {log}")


def quantizer_path_argument(path: Path, quantizer: Path) -> str:
    """Translate data paths when a Windows quantizer is invoked from WSL.

    WSL can start a ``.exe`` from ``/mnt`` directly, but the Windows process
    does not interpret its *arguments* as POSIX paths.  Converter output stays
    on the shared D: drive, so make that boundary explicit rather than relying
    on undocumented argument translation.
    """

    if sys.platform.startswith("linux") and quantizer.suffix.lower() == ".exe":
        result = subprocess.run(["wslpath", "-w", str(path)], text=True, capture_output=True, check=False)
        converted = result.stdout.strip()
        if result.returncode != 0 or not re.fullmatch(r"[A-Za-z]:\\.*", converted):
            raise ExportError(f"could not translate WSL path for Windows quantizer: {path}")
        return converted
    return str(path)


def validate_merged_model(path: Path) -> dict[str, Any]:
    """Verify the immutable merge manifest before any conversion is allowed."""

    if not path.is_dir():
        raise ExportError(f"merged HF model directory not found: {path}")
    required = ("config.json", "tokenizer_config.json")
    missing = [name for name in required if not (path / name).is_file()]
    has_weights = any(path.glob("*.safetensors")) or (path / "model.safetensors.index.json").is_file()
    if missing or not has_weights:
        raise ExportError(f"not a complete merged Hugging Face model: missing={missing}, weights={has_weights}")

    manifest_path = path / MERGE_MANIFEST_NAME
    if not manifest_path.is_file():
        raise ExportError(
            f"merged HF model is missing {MERGE_MANIFEST_NAME}; only a qualified merge_adapter.py output may be exported"
        )
    if manifest_path.is_symlink():
        raise ExportError("refusing a symlinked merge manifest")
    manifest = load_manifest(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("artifact_type") != "lastz_merged_hf_action_model":
        raise ExportError("merge manifest is not a supported Last Z merged Action V1 artifact")
    if manifest.get("status") != "passed" or manifest.get("format") != "safetensors":
        raise ExportError("merge manifest does not describe a completed safetensors merge")

    evaluation = manifest.get("holdout_evaluation")
    metrics = evaluation.get("production_metrics") if isinstance(evaluation, dict) else None
    gate = evaluation.get("production_gate") if isinstance(evaluation, dict) else None
    if not isinstance(metrics, dict) or not isinstance(gate, dict):
        raise ExportError("merge manifest lacks the required Action V1 holdout provenance")
    if not all(strict_one(metrics.get(name)) for name in ("schema_rate", "legal_rate", "exact_rate")):
        raise ExportError("merge manifest was not qualified by 100% production Action V1 metrics")
    if not all(gate.get(name) is True for name in ("schema_valid_100", "legal_100", "exact_100", "passed")):
        raise ExportError("merge manifest's production Action V1 gate did not pass")
    if not isinstance(evaluation.get("path"), str) or not is_sha256(evaluation.get("sha256")):
        raise ExportError("merge manifest lacks the checkpoint-evaluation report path/hash")
    adapter_provenance = manifest.get("adapter_provenance")
    evaluated_adapter = evaluation.get("adapter_provenance")
    if not isinstance(adapter_provenance, dict) or adapter_provenance != evaluated_adapter:
        raise ExportError("merge manifest adapter provenance does not match its qualified evaluation")

    provenance = manifest.get("merged_model_provenance")
    if not isinstance(provenance, dict):
        raise ExportError("merge manifest lacks merged_model_provenance")
    if provenance.get("hash_algorithm") != DIRECTORY_HASH_ALGORITHM:
        raise ExportError("merge manifest uses an unsupported merged-model hash algorithm")
    if provenance.get("local_path") != str(path):
        raise ExportError("merge manifest local path does not match the requested merged model")
    if not is_sha256(provenance.get("sha256")):
        raise ExportError("merge manifest has an invalid merged-model SHA-256")
    if not isinstance(provenance.get("file_count"), int) or isinstance(provenance["file_count"], bool):
        raise ExportError("merge manifest has an invalid merged-model file count")
    actual = fingerprint_directory(path, exclude_names=frozenset({MERGE_MANIFEST_NAME}))
    if provenance.get("sha256") != actual["sha256"] or provenance.get("file_count") != actual["file_count"]:
        raise ExportError("merged HF files changed after merge; refuse unprovenanced export")

    return {
        "path": str(manifest_path),
        "sha256": sha256_file(manifest_path),
        "manifest": manifest,
        "merged_model_provenance": actual,
    }


def ensure_output_safe(path: Path) -> None:
    if path.exists():
        raise ExportError(f"refusing to overwrite output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(path.parent).free < 14 * 1024**3:
        raise ExportError("less than 14 GiB free disk space for F16 conversion and Q6_K output")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged-model", type=Path, required=True)
    parser.add_argument("--name", default="lastz-qwen2.5-3b-action-v1")
    parser.add_argument("--llama-cpp-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--quantize", type=Path, default=discover_quantizer())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report_path = REPORTS / f"gguf_export_{timestamp}.json"
    log_path = REPORTS / f"gguf_export_{timestamp}.log"
    report: dict[str, Any] = {"started_at": now(), "status": "running"}
    try:
        if args.threads < 1:
            raise ExportError("--threads must be positive")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", args.name):
            raise ExportError("--name may contain only letters, digits, dot, underscore, and hyphen")
        merged_model = resolve_scoped_path(
            args.merged_model,
            parent=TRAINING_ROOT,
            scope=MERGED_ROOT,
            label="--merged-model",
        )
        merge_provenance = validate_merged_model(merged_model)
        output_dir = resolve_scoped_path(
            args.output_dir,
            parent=DEFAULT_OUT,
            scope=DEFAULT_OUT,
            label="--output-dir",
        )
        llama_cpp_dir = resolve_scoped_path(
            args.llama_cpp_dir,
            parent=BUNDLE_ROOT,
            scope=BUNDLE_ROOT / "tools",
            label="--llama-cpp-dir",
        )
        converter = llama_cpp_dir / "convert_hf_to_gguf.py"
        if not converter.is_file():
            raise ExportError(
                "missing llama.cpp HF converter. Clone the source checkout matching runtime b11430 "
                f"(expected commit prefix {EXPECTED_SOURCE_COMMIT}) under {llama_cpp_dir}, then install its conversion requirements."
            )
        if not args.quantize or not Path(args.quantize).is_file():
            raise ExportError("llama-quantize executable was not found; pass --quantize explicitly")
        quantizer = require_below(Path(args.quantize), BUNDLE_ROOT / "runtime", "--quantize")
        out_f16 = output_dir / f"{args.name}-f16.gguf"
        out_q6 = output_dir / f"{args.name}-Q6_K.gguf"
        output_manifest = output_dir / f"{args.name}-{GGUF_MANIFEST_NAME}"
        ensure_output_safe(out_f16)
        ensure_output_safe(out_q6)
        if output_manifest.exists():
            raise ExportError(f"refusing to overwrite GGUF provenance manifest: {output_manifest}")
        # A Q4 source is never an appropriate conversion input.  The actual
        # quantizer input is the newly generated F16 file; this check catches
        # a misleading user-supplied name before work begins.
        if re.search(r"(?:^|[-_.])Q[2-8](?:[_-]|$)", merged_model.name, re.IGNORECASE):
            raise ExportError("merged model path looks quantized; export must begin from full HF weights")
        convert_command = [
            str(args.python_executable),
            str(converter),
            str(merged_model),
            "--outtype",
            "f16",
            "--outfile",
            str(out_f16),
        ]
        quantize_command = [
            str(quantizer),
            quantizer_path_argument(out_f16, quantizer),
            quantizer_path_argument(out_q6, quantizer),
            "Q6_K",
            str(args.threads),
        ]
        report.update(
            merged_model=str(merged_model),
            merge_provenance=merge_provenance,
            converter=str(converter),
            quantizer=str(quantizer),
            expected_llama_cpp_commit_prefix=EXPECTED_SOURCE_COMMIT,
            f16_output=str(out_f16),
            q6_k_output=str(out_q6),
            gguf_manifest=str(output_manifest),
            conversion_command=convert_command,
            quantization_command=quantize_command,
            dry_run=bool(args.dry_run),
        )
        run_command(convert_command, log_path, dry_run=args.dry_run)
        if not args.dry_run and not out_f16.is_file():
            raise ExportError("converter exited successfully but did not create F16 GGUF")
        run_command(quantize_command, log_path, dry_run=args.dry_run)
        if not args.dry_run and not out_q6.is_file():
            raise ExportError("quantizer exited successfully but did not create Q6_K GGUF")
        if not args.dry_run:
            gguf_manifest = {
                "schema_version": 1,
                "artifact_type": "lastz_gguf_q6_k_action_model",
                "status": "passed",
                "created_at": now(),
                "source_merged_model": {
                    "path": str(merged_model),
                    "merge_manifest_path": merge_provenance["path"],
                    "merge_manifest_sha256": merge_provenance["sha256"],
                    "merged_model_provenance": merge_provenance["merged_model_provenance"],
                },
                "action_qualification": merge_provenance["manifest"]["holdout_evaluation"],
                "conversion": {
                    "flow": "merged_hf_safetensors -> f16_gguf -> Q6_K",
                    "converter": str(converter),
                    "quantizer": str(quantizer),
                    "llama_cpp_expected_commit_prefix": EXPECTED_SOURCE_COMMIT,
                    "threads": args.threads,
                },
                "artifacts": {
                    "f16": {"path": str(out_f16), "sha256": sha256_file(out_f16), "bytes": out_f16.stat().st_size},
                    "q6_k": {"path": str(out_q6), "sha256": sha256_file(out_q6), "bytes": out_q6.stat().st_size},
                },
            }
            write_json(output_manifest, gguf_manifest)
        report.update(
            status="passed" if not args.dry_run else "dry_run",
            finished_at=now(),
            f16_bytes=out_f16.stat().st_size if out_f16.is_file() else None,
            q6_k_bytes=out_q6.stat().st_size if out_q6.is_file() else None,
            log=str(log_path),
        )
        write_json(report_path, report)
        print(f"[OK] {report['status']}: {report_path}")
        return 0
    except Exception as exc:
        report.update(
            status="failed",
            finished_at=now(),
            error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
            log=str(log_path),
        )
        write_json(report_path, report)
        print(f"[FAILED] {report['error']}\nReport: {report_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
