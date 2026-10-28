#!/usr/bin/env python3
"""Export the pinned full Qwen2.5-3B-Instruct merge base to F16 and Q6_K.

This is deliberately separate from :mod:`export_gguf`.  It converts only the
immutable, full-precision Hugging Face merge base stored in this benchmark's
local Hugging Face cache.  It never accepts a GGUF/Q4 input, never starts
inference, and has no game, ADB, or BlueStacks integration.

The supported flow is strictly::

    pinned local HF safetensors -> F16 GGUF -> Q6_K GGUF

Both the source revision and the llama.cpp source/runtime release are checked
before conversion.  Outputs are never overwritten; an interrupted export is
left in place for manual inspection rather than silently deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
BUNDLE_ROOT = TRAINING_ROOT.parent
CACHE_ROOT = TRAINING_ROOT / "models" / "hf_cache"
OUTPUT_ROOT = TRAINING_ROOT / "outputs" / "gguf"
DEFAULT_OUTPUT_DIR = OUTPUT_ROOT / "base"
REPORTS = TRAINING_ROOT / "outputs" / "reports"
TOOLS_ROOT = BUNDLE_ROOT / "tools"
RUNTIME_ROOT = BUNDLE_ROOT / "runtime"

# This is the full safetensors merge base pinned in training/MODEL_SELECTION.md
# and used by merge_adapter.py.  It is intentionally not the dynamic Q4
# training checkpoint and not one of the benchmark GGUFs.
EXPECTED_REPOSITORY = "unsloth/Qwen2.5-3B-Instruct"
EXPECTED_CACHE_DIRECTORY = "models--unsloth--Qwen2.5-3B-Instruct"
EXPECTED_BASE_REVISION = "7548fff1f997f57b2e9e8ab1ec7be96949b00ed0"
DEFAULT_BASE_MODEL = CACHE_ROOT / EXPECTED_CACHE_DIRECTORY / "snapshots" / EXPECTED_BASE_REVISION

EXPECTED_LLAMA_CPP_COMMIT_PREFIX = "8345f3339"  # llama.cpp b11430
DEFAULT_LLAMA_CPP_DIR = TOOLS_ROOT / "llama.cpp-b11430"
DEFAULT_QUANTIZER = RUNTIME_ROOT / "llama-cpp-cuda-b11430" / "llama-quantize.exe"
BASE_MANIFEST_SUFFIX = "-lastz_base_gguf_manifest.json"
MIN_FREE_BYTES = 14 * 1024**3


class ExportError(RuntimeError):
    """A failed safety or provenance gate for base-model export."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_below(path: Path, parent: Path, label: str) -> Path:
    """Resolve *path* and ensure it cannot escape the benchmark bundle."""

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


def append_log(log: Path, message: str) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(message.rstrip("\n") + "\n")


def json_file_in_cache(path: Path, label: str) -> tuple[dict[str, Any], Path]:
    """Read a JSON cache entry, permitting HF blob symlinks only inside cache."""

    if not path.is_file():
        raise ExportError(f"missing required {label}: {path}")
    resolved = require_below(path, CACHE_ROOT, label)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"invalid {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"{label} must contain a JSON object: {path}")
    return value, resolved


def file_in_cache(path: Path, label: str) -> Path:
    """Return an existing file after rejecting cache-escaping symlinks."""

    if not path.is_file():
        raise ExportError(f"missing required {label}: {path}")
    return require_below(path, CACHE_ROOT, label)


def assert_base_layout(base_model: Path) -> None:
    """Require the exact local cache snapshot, not an arbitrary HF folder."""

    if not base_model.is_dir():
        raise ExportError(f"pinned base-model snapshot directory not found: {base_model}")
    if base_model.is_symlink():
        raise ExportError("--base-model must be the explicit cache snapshot directory, not a symlink")
    if base_model.name != EXPECTED_BASE_REVISION:
        raise ExportError(
            f"base snapshot revision must be {EXPECTED_BASE_REVISION}; got {base_model.name!r}"
        )
    snapshots = base_model.parent
    repository = snapshots.parent
    if snapshots.name != "snapshots" or repository.name != EXPECTED_CACHE_DIRECTORY:
        raise ExportError(
            "--base-model must be the pinned "
            f"{EXPECTED_REPOSITORY} snapshot below training/models/hf_cache"
        )


def validate_revision_evidence(base_model: Path) -> dict[str, Any]:
    """Capture the snapshot ID and verify cache refs/main when it is present."""

    repository = base_model.parent.parent
    refs_main = repository / "refs" / "main"
    evidence: dict[str, Any] = {
        "repository": EXPECTED_REPOSITORY,
        "expected_revision": EXPECTED_BASE_REVISION,
        "snapshot_directory": str(base_model),
        "snapshot_revision": base_model.name,
        "snapshot_revision_matches_expected": base_model.name == EXPECTED_BASE_REVISION,
        "refs_main_available": refs_main.is_file(),
    }
    if refs_main.is_file():
        concrete_ref = file_in_cache(refs_main, "HF cache refs/main")
        actual_ref = concrete_ref.read_text(encoding="utf-8").strip()
        evidence.update(refs_main=str(refs_main), refs_main_revision=actual_ref)
        if actual_ref != EXPECTED_BASE_REVISION:
            raise ExportError(
                "HF cache refs/main does not prove the pinned merge-base revision: "
                f"expected {EXPECTED_BASE_REVISION}, got {actual_ref!r}"
            )
    return evidence


def expect_config_value(config: dict[str, Any], key: str, expected: Any) -> None:
    if config.get(key) != expected:
        raise ExportError(
            f"base config does not identify Qwen2.5 3B Instruct: {key}="
            f"{config.get(key)!r}, expected {expected!r}"
        )


def validate_full_qwen_base(base_model: Path) -> dict[str, Any]:
    """Validate architecture and enumerate every full safetensors weight shard."""

    assert_base_layout(base_model)
    revision_evidence = validate_revision_evidence(base_model)
    config, config_path = json_file_in_cache(base_model / "config.json", "base config.json")
    expect_config_value(config, "model_type", "qwen2")
    architectures = config.get("architectures")
    if not isinstance(architectures, list) or "Qwen2ForCausalLM" not in architectures:
        raise ExportError("base config architectures must include Qwen2ForCausalLM")
    # These dimensions distinguish the 3B Qwen2.5 architecture from nearby
    # Qwen variants and make changing the source model an explicit decision.
    for key, value in {
        "hidden_size": 2048,
        "intermediate_size": 11008,
        "num_hidden_layers": 36,
        "num_attention_heads": 16,
        "num_key_value_heads": 2,
        "vocab_size": 151936,
        "max_position_embeddings": 32768,
        "tie_word_embeddings": True,
    }.items():
        expect_config_value(config, key, value)
    if "quantization_config" in config:
        raise ExportError("refusing a quantized base config; export requires full safetensors weights")
    config_origin = config.get("_name_or_path")
    allowed_origins = {"Qwen/Qwen2.5-3B-Instruct", EXPECTED_REPOSITORY}
    if config_origin not in allowed_origins:
        raise ExportError(
            "base config _name_or_path is not an approved Qwen2.5-3B-Instruct origin: "
            f"{config_origin!r}"
        )

    # The pinned base is sharded safetensors.  An index gives us an auditable
    # list of source weights and avoids accepting a GGUF or a BnB/Q4 folder.
    index, index_path = json_file_in_cache(
        base_model / "model.safetensors.index.json", "base model.safetensors.index.json"
    )
    weight_map = index.get("weight_map")
    if not isinstance(weight_map, dict) or not weight_map:
        raise ExportError("safetensors index has no non-empty weight_map")
    shard_names = sorted(set(weight_map.values()))
    if not all(isinstance(name, str) for name in shard_names):
        raise ExportError("safetensors index contains a non-string weight shard name")
    if any(
        Path(name).is_absolute()
        or Path(name).parent != Path(".")
        or not name.lower().endswith(".safetensors")
        for name in shard_names
    ):
        raise ExportError("safetensors index contains an unsafe or non-safetensors shard path")
    if any(re.search(r"(?:^|[-_.])q[2-8](?:[_-]|$)", name, re.IGNORECASE) for name in shard_names):
        raise ExportError("refusing a quantized/Q4-looking weight shard; full safetensors are required")
    gguf_inputs = list(base_model.glob("*.gguf"))
    if gguf_inputs:
        raise ExportError(f"refusing a GGUF source directory: found {gguf_inputs[0]}")
    quantization_sidecars = [
        item for item in (base_model / "quantization_config.json", base_model / "adapter_config.json") if item.exists()
    ]
    if quantization_sidecars:
        raise ExportError(f"refusing a quantized/adapter source sidecar: {quantization_sidecars[0]}")

    shards: list[dict[str, Any]] = []
    for name in shard_names:
        shard = file_in_cache(base_model / name, f"safetensors shard {name}")
        shards.append({"name": name, "resolved_path": str(shard), "bytes": shard.stat().st_size, "sha256": sha256_file(shard)})
    tokenizer_config = file_in_cache(base_model / "tokenizer_config.json", "base tokenizer_config.json")
    return {
        "source_kind": "pinned_local_huggingface_cache_full_safetensors",
        "repository": EXPECTED_REPOSITORY,
        "local_snapshot": str(base_model),
        "revision_evidence": revision_evidence,
        "config": {
            "path": str(config_path),
            "sha256": sha256_file(config_path),
            "model_type": config["model_type"],
            "architectures": architectures,
            "name_or_path": config_origin,
            "dimensions": {
                key: config[key]
                for key in (
                    "hidden_size",
                    "intermediate_size",
                    "num_hidden_layers",
                    "num_attention_heads",
                    "num_key_value_heads",
                    "vocab_size",
                    "max_position_embeddings",
                )
            },
        },
        "safetensors_index": {
            "path": str(index_path),
            "sha256": sha256_file(index_path),
            "weight_count": len(weight_map),
            "shard_count": len(shards),
        },
        "tokenizer_config": {"path": str(tokenizer_config), "sha256": sha256_file(tokenizer_config)},
        "weight_shards": shards,
    }


def command_output(command: list[str], label: str) -> str:
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise ExportError(f"could not run {label}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ExportError(f"{label} failed with exit code {result.returncode}: {detail}")
    return result.stdout.strip()


def validate_llama_cpp_source(path: Path) -> dict[str, Any]:
    """Require a clean, exact b11430 source checkout for the HF converter."""

    if not path.is_dir():
        raise ExportError(f"llama.cpp source directory not found: {path}")
    converter = path / "convert_hf_to_gguf.py"
    if not converter.is_file():
        raise ExportError(f"llama.cpp HF converter not found: {converter}")
    converter = require_below(converter, TOOLS_ROOT, "llama.cpp converter")
    commit = command_output(["git", "-C", str(path), "rev-parse", "HEAD"], "llama.cpp revision check")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not commit.startswith(EXPECTED_LLAMA_CPP_COMMIT_PREFIX):
        raise ExportError(
            "llama.cpp source must be b11430 at commit prefix "
            f"{EXPECTED_LLAMA_CPP_COMMIT_PREFIX}; got {commit!r}"
        )
    status = command_output(["git", "-C", str(path), "status", "--porcelain"], "llama.cpp cleanliness check")
    if status:
        raise ExportError("llama.cpp source checkout is dirty; refuse a non-reproducible converter")
    return {
        "path": str(path),
        "converter": str(converter),
        "commit": commit,
        "expected_commit_prefix": EXPECTED_LLAMA_CPP_COMMIT_PREFIX,
        "working_tree_clean": True,
    }


def validate_runtime_quantizer(value: Path) -> dict[str, Any]:
    """Require the isolated CUDA b11430 quantizer shipped with the benchmark."""

    quantizer = resolve_scoped_path(value, parent=RUNTIME_ROOT, scope=RUNTIME_ROOT, label="--quantize")
    expected_parent = (RUNTIME_ROOT / "llama-cpp-cuda-b11430").resolve()
    if quantizer.parent != expected_parent:
        raise ExportError(
            "--quantize must be the isolated CUDA b11430 runtime quantizer at "
            f"{expected_parent / 'llama-quantize.exe'}"
        )
    if quantizer.name.lower() != "llama-quantize.exe" or not quantizer.is_file():
        raise ExportError(f"llama.cpp CUDA b11430 quantizer not found: {quantizer}")
    return {
        "path": str(quantizer),
        "runtime_release": "llama.cpp b11430 CUDA",
        "bytes": quantizer.stat().st_size,
        "sha256": sha256_file(quantizer),
    }


def ensure_new_outputs(output_dir: Path, name: str, *, dry_run: bool) -> tuple[Path, Path, Path]:
    if output_dir.is_symlink():
        raise ExportError("--output-dir may not be a symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    f16 = output_dir / f"{name}-F16.gguf"
    q6_k = output_dir / f"{name}-Q6_K.gguf"
    manifest = output_dir / f"{name}{BASE_MANIFEST_SUFFIX}"
    for path in (f16, q6_k, manifest):
        # Resolve after the parent exists so a symlinked child cannot redirect
        # a write outside the scoped GGUF output directory.
        require_below(path, OUTPUT_ROOT, "output artifact")
        if path.exists() or path.is_symlink():
            raise ExportError(f"refusing to overwrite existing output: {path}")
    if not dry_run and shutil.disk_usage(output_dir).free < MIN_FREE_BYTES:
        raise ExportError("less than 14 GiB free disk space for F16 conversion and Q6_K output")
    return f16, q6_k, manifest


def run_command(command: list[str], log: Path, *, dry_run: bool) -> None:
    append_log(log, "$ " + json.dumps(command, ensure_ascii=False))
    if dry_run:
        append_log(log, "[dry-run: command not executed]")
        return
    with log.open("a", encoding="utf-8") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, text=True, check=False)
    if result.returncode != 0:
        raise ExportError(f"command failed with exit code {result.returncode}; see {log}")


def quantizer_path_argument(path: Path, quantizer: Path) -> str:
    """Translate data paths when the Windows quantizer is called from WSL."""

    if sys.platform.startswith("linux") and quantizer.suffix.lower() == ".exe":
        result = subprocess.run(["wslpath", "-w", str(path)], text=True, capture_output=True, check=False)
        converted = result.stdout.strip()
        if result.returncode != 0 or not re.fullmatch(r"[A-Za-z]:\\.*", converted):
            raise ExportError(f"could not translate WSL path for Windows quantizer: {path}")
        return converted
    return str(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-model",
        type=Path,
        default=DEFAULT_BASE_MODEL,
        help="exact local pinned snapshot below training/models/hf_cache",
    )
    parser.add_argument("--name", default="qwen2.5-3b-instruct-base")
    parser.add_argument("--llama-cpp-dir", type=Path, default=DEFAULT_LLAMA_CPP_DIR)
    parser.add_argument("--quantize", type=Path, default=DEFAULT_QUANTIZER)
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="validate and log commands without converting")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report_path = REPORTS / f"base_gguf_export_{timestamp}.json"
    log_path = REPORTS / f"base_gguf_export_{timestamp}.log"
    report: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "lastz_base_gguf_export",
        "started_at": now(),
        "status": "running",
        "safety": {
            "flow": "pinned_local_hf_safetensors -> F16 GGUF -> Q6_K",
            "q4_or_gguf_input_allowed": False,
            "game_or_adb_interaction": False,
            "overwrite_allowed": False,
        },
    }
    append_log(log_path, f"[{now()}] Last Z base GGUF export started")
    try:
        if args.threads < 1:
            raise ExportError("--threads must be positive")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", args.name):
            raise ExportError("--name may contain only letters, digits, dot, underscore, and hyphen")

        base_model = resolve_scoped_path(
            args.base_model, parent=TRAINING_ROOT, scope=CACHE_ROOT, label="--base-model"
        )
        output_dir = resolve_scoped_path(
            args.output_dir, parent=OUTPUT_ROOT, scope=OUTPUT_ROOT, label="--output-dir"
        )
        llama_cpp_dir = resolve_scoped_path(
            args.llama_cpp_dir, parent=BUNDLE_ROOT, scope=TOOLS_ROOT, label="--llama-cpp-dir"
        )
        source_provenance = validate_full_qwen_base(base_model)
        llama_cpp = validate_llama_cpp_source(llama_cpp_dir)
        quantizer = validate_runtime_quantizer(args.quantize)
        f16_output, q6_k_output, manifest_path = ensure_new_outputs(
            output_dir, args.name, dry_run=bool(args.dry_run)
        )

        convert_command = [
            str(args.python_executable),
            llama_cpp["converter"],
            str(base_model),
            "--outtype",
            "f16",
            "--outfile",
            str(f16_output),
        ]
        quantizer_path = Path(str(quantizer["path"]))
        quantize_command = [
            str(quantizer_path),
            quantizer_path_argument(f16_output, quantizer_path),
            quantizer_path_argument(q6_k_output, quantizer_path),
            "Q6_K",
            str(args.threads),
        ]
        report.update(
            base_model=str(base_model),
            source_provenance=source_provenance,
            llama_cpp=llama_cpp,
            quantizer=quantizer,
            output_dir=str(output_dir),
            f16_output=str(f16_output),
            q6_k_output=str(q6_k_output),
            manifest_path=str(manifest_path),
            conversion_command=convert_command,
            quantization_command=quantize_command,
            dry_run=bool(args.dry_run),
        )
        run_command(convert_command, log_path, dry_run=bool(args.dry_run))
        if not args.dry_run and not f16_output.is_file():
            raise ExportError("converter exited successfully but did not create F16 GGUF")
        run_command(quantize_command, log_path, dry_run=bool(args.dry_run))
        if not args.dry_run and not q6_k_output.is_file():
            raise ExportError("quantizer exited successfully but did not create Q6_K GGUF")

        if not args.dry_run:
            manifest = {
                "schema_version": 1,
                "artifact_type": "lastz_base_qwen2.5_3b_instruct_q6_k",
                "status": "passed",
                "created_at": now(),
                "source_base": source_provenance,
                "conversion": {
                    "flow": "pinned_local_hf_safetensors -> F16 GGUF -> Q6_K",
                    "llama_cpp": llama_cpp,
                    "quantizer": quantizer,
                    "threads": args.threads,
                },
                "artifacts": {
                    "f16": {
                        "path": str(f16_output),
                        "bytes": f16_output.stat().st_size,
                        "sha256": sha256_file(f16_output),
                    },
                    "q6_k": {
                        "path": str(q6_k_output),
                        "bytes": q6_k_output.stat().st_size,
                        "sha256": sha256_file(q6_k_output),
                    },
                },
            }
            write_json(manifest_path, manifest)
            report["manifest_sha256"] = sha256_file(manifest_path)
        report.update(
            status="dry_run" if args.dry_run else "passed",
            finished_at=now(),
            f16_bytes=f16_output.stat().st_size if f16_output.is_file() else None,
            q6_k_bytes=q6_k_output.stat().st_size if q6_k_output.is_file() else None,
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
