#!/usr/bin/env python3
"""Run the existing isolated CUDA benchmark on base and fine-tuned Q6_K GGUFs.

This is a thin reproducibility wrapper; scoring, server logs, VRAM telemetry,
and Markdown/JSON comparison reports remain owned by the existing benchmark
runner.  No Vulkan rerun, ADB, BlueStacks, or game action is involved.
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
BUNDLE_ROOT = TRAINING_ROOT.parent
RUNNER = BUNDLE_ROOT / "benchmarks" / "run_performance_benchmark.py"
CUDA_SERVER = next(iter(sorted((BUNDLE_ROOT / "runtime").glob("llama-cpp-cuda-*/llama-server.exe"), reverse=True)), None)
REPORTS = TRAINING_ROOT / "outputs" / "reports"

# ``general.file_type`` is a GGUF metadata field written by llama.cpp.  The
# numeric value is stable across the GGUF versions accepted below; 18 is
# GGML_FTYPE_MOSTLY_Q6_K.  Do not infer a quantization type from a filename.
GGUF_MAGIC = b"GGUF"
GGUF_SUPPORTED_VERSIONS = {2, 3}
GGUF_FILE_TYPE_Q6_K = 18
GGUF_FILE_TYPE_NAMES = {GGUF_FILE_TYPE_Q6_K: "Q6_K"}
GGUF_FIXED_VALUE_FORMATS = {
    0: "<B",   # uint8
    1: "<b",   # int8
    2: "<H",   # uint16
    3: "<h",   # int16
    4: "<I",   # uint32
    5: "<i",   # int32
    6: "<f",   # float32
    7: "<?",   # bool
    10: "<Q",  # uint64
    11: "<q",  # int64
    12: "<d",  # float64
}
GGUF_METADATA_KEYS = {
    "general.architecture",
    "general.file_type",
    "general.name",
    "general.quantization_version",
    "tokenizer.chat_template",
    "tokenizer.ggml.model",
    "tokenizer.ggml.pre",
    "qwen2.block_count",
    "qwen2.context_length",
    "qwen2.embedding_length",
    "qwen2.feed_forward_length",
    "qwen2.attention.head_count",
    "qwen2.attention.head_count_kv",
    "qwen2.rope.freq_base",
    "qwen2.attention.layer_norm_rms_epsilon",
}
QWEN25_3B_FINGERPRINT = {
    "general.architecture": "qwen2",
    "qwen2.block_count": 36,
    "qwen2.context_length": 32768,
    "qwen2.embedding_length": 2048,
    "qwen2.feed_forward_length": 11008,
    "qwen2.attention.head_count": 16,
    "qwen2.attention.head_count_kv": 2,
}


class BenchmarkGateError(RuntimeError):
    """A completed run is not promotable to an Action V1 production result."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_exact(stream: Any, count: int, path: Path) -> bytes:
    value = stream.read(count)
    if len(value) != count:
        raise BenchmarkGateError(f"truncated GGUF metadata in {path}")
    return value


def _read_u32(stream: Any, path: Path) -> int:
    return int(struct.unpack("<I", _read_exact(stream, 4, path))[0])


def _read_u64(stream: Any, path: Path) -> int:
    return int(struct.unpack("<Q", _read_exact(stream, 8, path))[0])


def _skip_bytes(stream: Any, count: int, path: Path, file_size: int) -> None:
    position = stream.tell()
    if count < 0 or position + count > file_size:
        raise BenchmarkGateError(f"invalid GGUF metadata length in {path}")
    stream.seek(count, 1)


def _read_string(stream: Any, path: Path, file_size: int) -> str:
    count = _read_u64(stream, path)
    if count > 64 * 1024 * 1024:
        raise BenchmarkGateError(f"unreasonably large GGUF string in {path}")
    if stream.tell() + count > file_size:
        raise BenchmarkGateError(f"invalid GGUF metadata length in {path}")
    return _read_exact(stream, count, path).decode("utf-8", errors="strict")


def _skip_gguf_value(stream: Any, value_type: int, path: Path, file_size: int, depth: int = 0) -> None:
    """Skip a GGUF metadata value without materializing tokenizer arrays."""

    if depth > 4:
        raise BenchmarkGateError(f"unsupported nested GGUF metadata array in {path}")
    if value_type in GGUF_FIXED_VALUE_FORMATS:
        _skip_bytes(stream, struct.calcsize(GGUF_FIXED_VALUE_FORMATS[value_type]), path, file_size)
        return
    if value_type == 8:  # string
        count = _read_u64(stream, path)
        _skip_bytes(stream, count, path, file_size)
        return
    if value_type != 9:  # array
        raise BenchmarkGateError(f"unsupported GGUF metadata value type {value_type} in {path}")
    element_type = _read_u32(stream, path)
    count = _read_u64(stream, path)
    if count > 10_000_000:
        raise BenchmarkGateError(f"unreasonably large GGUF metadata array in {path}")
    if element_type in GGUF_FIXED_VALUE_FORMATS:
        _skip_bytes(
            stream,
            count * struct.calcsize(GGUF_FIXED_VALUE_FORMATS[element_type]),
            path,
            file_size,
        )
        return
    for _ in range(count):
        _skip_gguf_value(stream, element_type, path, file_size, depth + 1)


def _read_gguf_value(stream: Any, value_type: int, path: Path, file_size: int) -> Any:
    if value_type in GGUF_FIXED_VALUE_FORMATS:
        return struct.unpack(
            GGUF_FIXED_VALUE_FORMATS[value_type],
            _read_exact(stream, struct.calcsize(GGUF_FIXED_VALUE_FORMATS[value_type]), path),
        )[0]
    if value_type == 8:
        return _read_string(stream, path, file_size)
    # None of the identity fields we intentionally retain is an array.  Still
    # consume it correctly before failing, so a malformed/truncated artifact
    # cannot be mistaken for a valid Q6_K file.
    _skip_gguf_value(stream, value_type, path, file_size)
    raise BenchmarkGateError(f"unexpected array metadata value in {path}")


def read_gguf_metadata(path: Path) -> dict[str, Any]:
    """Read only the small, signed-in-file GGUF metadata header.

    This deliberately avoids loading tensors or invoking llama.cpp.  It is a
    local artifact identity check, not a model inference operation.
    """

    file_size = path.stat().st_size
    try:
        with path.open("rb") as stream:
            if _read_exact(stream, 4, path) != GGUF_MAGIC:
                raise BenchmarkGateError(f"not a GGUF file: {path}")
            version = _read_u32(stream, path)
            if version not in GGUF_SUPPORTED_VERSIONS:
                raise BenchmarkGateError(
                    f"unsupported GGUF version {version} in {path}; expected one of {sorted(GGUF_SUPPORTED_VERSIONS)}"
                )
            tensor_count = _read_u64(stream, path)
            metadata_count = _read_u64(stream, path)
            if metadata_count > 100_000:
                raise BenchmarkGateError(f"unreasonably large GGUF metadata table in {path}")
            metadata: dict[str, Any] = {}
            for _ in range(metadata_count):
                key = _read_string(stream, path, file_size)
                value_type = _read_u32(stream, path)
                if key in GGUF_METADATA_KEYS:
                    metadata[key] = _read_gguf_value(stream, value_type, path, file_size)
                else:
                    _skip_gguf_value(stream, value_type, path, file_size)
    except (OSError, UnicodeDecodeError, struct.error) as exc:
        raise BenchmarkGateError(f"could not read GGUF metadata from {path}: {exc}") from exc
    return {
        "path": str(path),
        "size_bytes": file_size,
        "gguf_version": version,
        "tensor_count": tensor_count,
        "metadata": metadata,
    }


def inspect_qwen25_3b_q6(path: Path, label: str) -> dict[str, Any]:
    artifact = read_gguf_metadata(path)
    metadata = artifact["metadata"]
    file_type = metadata.get("general.file_type")
    if isinstance(file_type, bool) or file_type != GGUF_FILE_TYPE_Q6_K:
        actual = GGUF_FILE_TYPE_NAMES.get(file_type, f"unknown file_type={file_type!r}")
        raise BenchmarkGateError(
            f"{label} artifact is not metadata-verified Q6_K ({actual}): {path}"
        )
    quantization_version = metadata.get("general.quantization_version")
    if isinstance(quantization_version, bool) or not isinstance(quantization_version, int):
        raise BenchmarkGateError(f"{label} Q6_K artifact has no integer general.quantization_version: {path}")
    missing_or_mismatched = {
        key: {"expected": expected, "actual": metadata.get(key)}
        for key, expected in QWEN25_3B_FINGERPRINT.items()
        if metadata.get(key) != expected
    }
    if missing_or_mismatched:
        raise BenchmarkGateError(
            f"{label} artifact is not the expected Qwen2.5 3B architecture: {missing_or_mismatched}"
        )
    template = metadata.get("tokenizer.chat_template")
    if not isinstance(template, str) or not template.strip():
        raise BenchmarkGateError(f"{label} artifact lacks tokenizer.chat_template required by llama.cpp --jinja")
    artifact["quantization"] = {
        "file_type": file_type,
        "name": GGUF_FILE_TYPE_NAMES[file_type],
        "quantization_version": quantization_version,
    }
    artifact["architecture_fingerprint"] = {
        key: metadata[key] for key in QWEN25_3B_FINGERPRINT
    }
    return artifact


def verify_matching_model_pair(base: dict[str, Any], fine_tuned: dict[str, Any]) -> None:
    if base["architecture_fingerprint"] != fine_tuned["architecture_fingerprint"]:
        raise BenchmarkGateError("base and fine-tuned Q6_K artifacts have different Qwen2.5 3B architecture metadata")
    base_template = base["metadata"].get("tokenizer.chat_template")
    fine_template = fine_tuned["metadata"].get("tokenizer.chat_template")
    if base_template != fine_template:
        raise BenchmarkGateError(
            "base and fine-tuned Q6_K artifacts have different tokenizer.chat_template metadata; "
            "a direct action comparison would be confounded"
        )


def parse_cache_modes(value: str) -> list[str]:
    modes = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not modes or not set(modes) <= {"disabled", "enabled"}:
        raise ValueError("cache-modes must contain disabled and/or enabled")
    return modes


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _result_model_path(result: dict[str, Any]) -> Path | None:
    model = result.get("model")
    raw_path = model.get("path") if isinstance(model, dict) else None
    if not isinstance(raw_path, str):
        return None
    return Path(raw_path).expanduser().resolve()


def _telemetry_issues(result: dict[str, Any], minimum_headroom_mib: float) -> list[str]:
    issues: list[str] = []
    vram = result.get("vram") if isinstance(result.get("vram"), dict) else {}
    for label in ("before_model_load", "after_model_load"):
        snapshot = vram.get(label)
        if not isinstance(snapshot, dict):
            issues.append(f"missing {label} VRAM snapshot")
            continue
        for key in ("memory_total_mib", "memory_used_mib", "memory_free_mib"):
            if _number(snapshot.get(key)) is None:
                issues.append(f"{label}.{key} missing")
    during = vram.get("during_generation")
    if not isinstance(during, dict):
        issues.append("missing during_generation VRAM telemetry")
        return issues
    if not isinstance(during.get("sample_count"), int) or during["sample_count"] < 1:
        issues.append("during_generation has no VRAM samples")
    if _number(during.get("peak_memory_used_mib")) is None:
        issues.append("during_generation.peak_memory_used_mib missing")
    headroom = _number(during.get("minimum_memory_free_mib"))
    if headroom is None:
        issues.append("during_generation.minimum_memory_free_mib missing")
    elif headroom < minimum_headroom_mib:
        issues.append(f"VRAM headroom {headroom:.1f} MiB is below required {minimum_headroom_mib:.1f} MiB")
    return issues


def _common_result_issues(result: dict[str, Any], expected_context: int, minimum_headroom_mib: float) -> list[str]:
    issues: list[str] = []
    if result.get("error"):
        issues.append(f"runner error: {result['error']}")
    if result.get("context") != expected_context:
        issues.append(f"unexpected context {result.get('context')!r}")
    backend = result.get("backend") if isinstance(result.get("backend"), dict) else {}
    evidence = result.get("backend_evidence") if isinstance(result.get("backend_evidence"), dict) else {}
    if backend.get("requested") != "cuda":
        issues.append(f"requested backend is not CUDA: {backend.get('requested')!r}")
    if evidence.get("verified") is not True:
        issues.append("CUDA backend was not verified from llama-server evidence")
    if evidence.get("full_gpu_offload") is not True:
        issues.append("full GPU layer offload was not verified")
    if not isinstance(result.get("summary"), dict):
        issues.append("missing benchmark summary")
    issues.extend(_telemetry_issues(result, minimum_headroom_mib))
    return issues


def _fine_tuned_qualification_issues(result: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    production = summary.get("production_qualification") if isinstance(summary.get("production_qualification"), dict) else {}
    warm = summary.get("warm") if isinstance(summary.get("warm"), dict) else {}
    required = {
        "cold_first_hit_pass": production.get("cold_first_hit_pass"),
        "qualified": production.get("qualified"),
        "warm.valid_json_rate": warm.get("valid_json_rate") == 1.0,
        "warm.schema_valid_rate": warm.get("schema_valid_rate") == 1.0,
        "warm.legal_action_rate": warm.get("legal_action_rate") == 1.0,
        "warm.exact_match_rate": warm.get("exact_match_rate") == 1.0,
        "warm.selector_message_free_rate": warm.get("selector_message_free_rate") == 1.0,
        "warm.errors": warm.get("errors") == 0,
    }
    issues.extend(f"production gate failed: {key}" for key, passed in required.items() if passed is not True)
    return issues


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    warm = summary.get("warm") if isinstance(summary.get("warm"), dict) else {}
    vram = result.get("vram") if isinstance(result.get("vram"), dict) else {}
    during = vram.get("during_generation") if isinstance(vram.get("during_generation"), dict) else {}
    return {
        "cache_mode": result.get("cache_mode"),
        "context": result.get("context"),
        "backend_evidence": result.get("backend_evidence"),
        "production_qualification": summary.get("production_qualification"),
        "warm_action_metrics": {
            key: warm.get(key)
            for key in (
                "valid_json_rate",
                "schema_valid_rate",
                "legal_action_rate",
                "exact_match_rate",
                "selector_message_free_rate",
                "errors",
                "end_to_end_ms",
            )
        },
        "vram": {
            "before_model_load": vram.get("before_model_load"),
            "after_model_load": vram.get("after_model_load"),
            "during_generation": during,
        },
    }


def load_comparison_report(runner_report_dir: Path) -> tuple[Path, dict[str, Any]]:
    reports = sorted(runner_report_dir.glob("performance_*/comparison.json"))
    if len(reports) != 1:
        raise BenchmarkGateError(
            f"expected exactly one runner comparison.json below {runner_report_dir}; found {len(reports)}"
        )
    path = reports[0]
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkGateError(f"cannot read runner comparison report {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise BenchmarkGateError(f"runner comparison report is not an object: {path}")
    return path, report


def verify_runner_evidence(
    comparison: dict[str, Any],
    *,
    base_model: Path,
    fine_tuned_model: Path,
    context: int,
    cache_modes: list[str],
) -> dict[str, Any]:
    """Require traceable CUDA Action V1 evidence, not just runner exit status."""

    configuration = comparison.get("configuration") if isinstance(comparison.get("configuration"), dict) else {}
    issues: list[str] = []
    if configuration.get("backends") != ["cuda"]:
        issues.append(f"comparison did not record CUDA-only backend selection: {configuration.get('backends')!r}")
    if configuration.get("action_contexts") != [context]:
        issues.append(f"comparison action contexts do not match requested [{context}]")
    if configuration.get("cache_modes") != cache_modes:
        issues.append(f"comparison cache modes do not match requested {cache_modes!r}")
    if configuration.get("max_action_tokens") != 24:
        issues.append("comparison did not retain the 24-token selector cap")
    if configuration.get("gpu_layers_requested") != 99:
        issues.append("comparison did not request full GPU offload (--gpu-layers 99)")
    minimum_headroom = _number(configuration.get("minimum_vram_headroom_mib"))
    if minimum_headroom is None or minimum_headroom < 0:
        issues.append("comparison has no valid minimum VRAM headroom configuration")
        minimum_headroom = float("inf")

    results = comparison.get("results")
    if not isinstance(results, list):
        issues.append("comparison has no result list")
        results = []

    def matching(model: Path, cache_mode: str) -> list[dict[str, Any]]:
        return [
            result
            for result in results
            if isinstance(result, dict)
            and result.get("suite") == "production_action_suite"
            and result.get("cache_mode") == cache_mode
            and _result_model_path(result) == model
        ]

    evidence: dict[str, list[dict[str, Any]]] = {"base": [], "fine_tuned": []}
    for label, model, require_qualification in (
        ("base", base_model, False),
        ("fine_tuned", fine_tuned_model, True),
    ):
        for cache_mode in cache_modes:
            matches = matching(model, cache_mode)
            if len(matches) != 1:
                issues.append(
                    f"{label} model must have exactly one production_action result for cache={cache_mode}; found {len(matches)}"
                )
                continue
            result = matches[0]
            result_issues = _common_result_issues(result, context, minimum_headroom)
            if require_qualification:
                result_issues.extend(_fine_tuned_qualification_issues(result))
            if result_issues:
                issues.extend(f"{label} cache={cache_mode}: {issue}" for issue in result_issues)
            evidence[label].append(_result_summary(result))

    if issues:
        raise BenchmarkGateError("; ".join(issues))
    return {
        "passed": True,
        "minimum_vram_headroom_mib": minimum_headroom,
        "base": evidence["base"],
        "fine_tuned": evidence["fine_tuned"],
        "runner_recommendation": (comparison.get("recommendations") or {}).get("action_production_configuration"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", type=Path, required=True, help="Base Qwen2.5 3B Q6_K GGUF.")
    parser.add_argument("--fine-tuned-model", type=Path, required=True, help="Merged Action V1 Q6_K GGUF.")
    parser.add_argument("--server-cuda", type=Path, default=CUDA_SERVER)
    parser.add_argument("--context", type=int, default=1024)
    parser.add_argument("--cache-modes", default="disabled,enabled")
    parser.add_argument("--action-rounds", type=int, default=3)
    parser.add_argument("--chat-rounds", type=int, default=3)
    parser.add_argument("--with-chat", action="store_true", help="Only use after Chat V1 is approved.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = REPORTS / f"q6_benchmark_{stamp}.json"
    runner_report_dir = REPORTS / f"q6_benchmark_{stamp}_runner"
    report: dict[str, Any] = {"started_at": now(), "status": "running"}
    try:
        if args.context not in {1024, 2048, 4096}:
            raise ValueError("context must be one of 1024, 2048, 4096")
        if args.action_rounds < 1 or args.chat_rounds < 1:
            raise ValueError("round counts must be positive")
        cache_modes = parse_cache_modes(args.cache_modes)
        if not RUNNER.is_file():
            raise FileNotFoundError(f"existing benchmark runner missing: {RUNNER}")
        if not args.server_cuda or not args.server_cuda.is_file():
            raise FileNotFoundError("CUDA llama-server missing; pass --server-cuda")
        base_model = args.base_model.expanduser().resolve()
        fine_tuned_model = args.fine_tuned_model.expanduser().resolve()
        for label, model in (("base", base_model), ("fine-tuned", fine_tuned_model)):
            if not model.is_file():
                raise FileNotFoundError(f"{label} GGUF missing: {model}")
        base_artifact = inspect_qwen25_3b_q6(base_model, "base")
        fine_tuned_artifact = inspect_qwen25_3b_q6(fine_tuned_model, "fine-tuned")
        verify_matching_model_pair(base_artifact, fine_tuned_artifact)
        suites = "production_action,adversarial_action" + (",chat" if args.with_chat else "")
        command = [
            sys.executable,
            str(RUNNER),
            "--suites",
            suites,
            "--backends",
            "cuda",
            "--server-cuda",
            str(args.server_cuda),
            "--model",
            str(base_model),
            "--model",
            str(fine_tuned_model),
            "--action-contexts",
            str(args.context),
            "--chat-contexts",
            str(args.context),
            "--cache-modes",
            ",".join(cache_modes),
            "--gpu-layers",
            "99",
            "--max-action-tokens",
            "24",
            "--action-rounds",
            str(args.action_rounds),
            "--chat-rounds",
            str(args.chat_rounds),
            "--warmup",
            "2",
            "--speculative",
            "off",
            "--report-dir",
            str(runner_report_dir),
        ]
        if args.with_chat:
            command.extend(["--chat-modes", "streaming,non_streaming"])
        report.update(
            base_model=str(base_model),
            fine_tuned_model=str(fine_tuned_model),
            artifacts={
                "base": base_artifact,
                "fine_tuned": fine_tuned_artifact,
                "pair_verified": True,
            },
            server_cuda=str(args.server_cuda),
            suites=suites,
            command=command,
            runner_report_dir=str(runner_report_dir),
            action_production_gate=(
                "fine-tuned model: cold and warm JSON/schema/legal/exact/selector-message-free=100%, "
                "verified CUDA full offload, and complete before/load/generation VRAM telemetry with required headroom"
            ),
            adversarial_reported_separately=True,
            chat_requires_action_v1_qualification=True,
            requested_cache_modes=cache_modes,
        )
        if args.dry_run:
            report.update(status="dry_run", finished_at=now())
            write_json(manifest_path, report)
            print(f"[OK] dry run: {manifest_path}")
            return 0
        process = subprocess.run(command, check=False)
        if process.returncode:
            report.update(status="failed", returncode=process.returncode, finished_at=now())
            write_json(manifest_path, report)
            print(f"[FAILED] runner exit={process.returncode}; manifest: {manifest_path}", file=sys.stderr)
            return process.returncode
        comparison_path, comparison = load_comparison_report(runner_report_dir)
        qualification = verify_runner_evidence(
            comparison,
            base_model=base_model,
            fine_tuned_model=fine_tuned_model,
            context=args.context,
            cache_modes=cache_modes,
        )
        report.update(
            status="passed",
            returncode=process.returncode,
            finished_at=now(),
            comparison_report=str(comparison_path),
            production_evidence=qualification,
        )
        write_json(manifest_path, report)
        print(f"[OK] Q6 comparison reports: {runner_report_dir}")
        return 0
    except BenchmarkGateError as exc:
        report.update(
            status="blocked",
            finished_at=now(),
            gate_passed=False,
            error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )
        write_json(manifest_path, report)
        print(f"[BLOCKED] {report['error']}\nManifest: {manifest_path}", file=sys.stderr)
        return 2
    except Exception as exc:
        report.update(
            status="failed",
            finished_at=now(),
            error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
        )
        write_json(manifest_path, report)
        print(f"[FAILED] {report['error']}\nManifest: {manifest_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
