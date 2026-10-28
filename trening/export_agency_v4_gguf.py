"""Create a separately named Q6_K GGUF from a completed PEva V4 adapter.

This is an explicit test export.  It never changes config/models.json, the
runtime chat selection, or any auto-send setting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_ID = "Qwen/Qwen2.5-7B-Instruct"
BASE_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
DEFAULT_ADAPTER = ROOT / "data" / "training" / "runs" / "peva-qwen2.5-7b-agency-pilot-v4" / "adapter"
EXPORT_ROOT = ROOT / "data" / "training" / "exports" / "peva-agency-pilot-v4-test"
MERGED_DIR = EXPORT_ROOT / "merged_hf_f16"
F16_GGUF = EXPORT_ROOT / "peva-agency-pilot-v4-test-F16.gguf"
Q6_GGUF = EXPORT_ROOT / "peva-agency-pilot-v4-test-Q6_K.gguf"
REPORT = EXPORT_ROOT / "export_report.json"
CONVERTER = ROOT / "lastz_model_benchmark" / "tools" / "llama.cpp-b11430" / "convert_hf_to_gguf.py"
QUANTIZER = ROOT / "lastz_model_benchmark" / "runtime" / "llama-cpp-cuda-b11430" / "llama-quantize.exe"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_adapter(adapter: Path) -> dict[str, Any]:
    metrics = adapter.parent / "metrics.json"
    if not adapter.is_dir() or not metrics.is_file():
        raise RuntimeError(f"Completed V4 adapter is missing: {adapter}")
    result = json.loads(metrics.read_text(encoding="utf-8"))
    if result.get("status") != "completed":
        raise RuntimeError("V4 adapter does not have a completed training report.")
    return result


def preflight(adapter: Path) -> dict[str, Any]:
    metrics = _completed_adapter(adapter)
    missing = [str(path) for path in (CONVERTER, QUANTIZER) if not path.is_file()]
    if EXPORT_ROOT.exists():
        raise RuntimeError(f"Refusing to overwrite an existing V4 test export: {EXPORT_ROOT}")
    if missing:
        raise RuntimeError(f"Missing GGUF conversion tool(s): {missing}")
    free = shutil.disk_usage(ROOT).free
    # A 7B F16 merged model plus F16/Q6 GGUF output needs substantially more
    # than the final Q6_K file alone.  Keep a conservative local-space gate.
    if free < 35 * 1024**3:
        raise RuntimeError("Need at least 35 GiB free disk space for a safe temporary 7B GGUF export.")
    return {
        "ready": True,
        "adapter": str(adapter),
        "adapter_sha256": _sha256(adapter / "adapter_model.safetensors"),
        "training_completed_at": metrics.get("completed_at_utc"),
        "base_model": {"id": BASE_ID, "revision": BASE_REVISION},
        "export": {"directory": str(EXPORT_ROOT), "quantization": "Q6_K", "runtime_model_replaced": False},
        "disk_free_gib": round(free / 1024**3, 2),
    }


def _windows_path(path: Path) -> str:
    """Return a Windows-native path for the bundled Windows quantizer."""
    return subprocess.run(
        ["wslpath", "-w", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _finish_export(report: dict[str, Any]) -> dict[str, Any]:
    if not Q6_GGUF.is_file() or Q6_GGUF.stat().st_size < 1024**3:
        raise RuntimeError("Q6_K GGUF was not created or is implausibly small.")
    report.update({
        "status": "completed",
        "completed_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "gguf": {"path": str(Q6_GGUF), "size_bytes": Q6_GGUF.stat().st_size, "sha256": _sha256(Q6_GGUF), "quantization": "Q6_K"},
        "temporary_f16_removed": True,
        "temporary_merged_hf_removed": True,
    })
    F16_GGUF.unlink(missing_ok=True)
    shutil.rmtree(MERGED_DIR, ignore_errors=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def export(adapter: Path) -> dict[str, Any]:
    report = preflight(adapter)
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    EXPORT_ROOT.mkdir(parents=True, exist_ok=False)
    try:
        tokenizer = AutoTokenizer.from_pretrained(adapter, local_files_only=True, use_fast=True)
        base = AutoModelForCausalLM.from_pretrained(
            BASE_ID,
            revision=BASE_REVISION,
            local_files_only=True,
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
            device_map="cpu",
        )
        tuned = PeftModel.from_pretrained(base, adapter, local_files_only=True)
        merged = tuned.merge_and_unload(safe_merge=True)
        merged.save_pretrained(MERGED_DIR, safe_serialization=True, max_shard_size="4GB")
        tokenizer.save_pretrained(MERGED_DIR)
        subprocess.run(
            [sys.executable, str(CONVERTER), "--outfile", str(F16_GGUF), "--outtype", "f16", str(MERGED_DIR)],
            check=True,
        )
        subprocess.run(
            [str(QUANTIZER), _windows_path(F16_GGUF), _windows_path(Q6_GGUF), "Q6_K"],
            check=True,
        )
        return _finish_export(report)
    except Exception:
        # Preserve an incomplete export directory for diagnosis; never replace
        # it on a later run.  It has no effect on the configured runtime.
        raise


def resume_quantization(adapter: Path) -> dict[str, Any]:
    """Finish a failed path-translation export without repeating the HF merge."""
    metrics = _completed_adapter(adapter)
    missing = [str(path) for path in (QUANTIZER, F16_GGUF) if not path.is_file()]
    if missing:
        raise RuntimeError(f"Cannot resume V4 GGUF quantization; missing: {missing}")
    if Q6_GGUF.exists() or REPORT.exists():
        raise RuntimeError("Refusing to overwrite an existing V4 Q6_K GGUF or export report.")
    report = {
        "ready": True,
        "resumed_after_path_translation_fix": True,
        "adapter": str(adapter),
        "adapter_sha256": _sha256(adapter / "adapter_model.safetensors"),
        "training_completed_at": metrics.get("completed_at_utc"),
        "base_model": {"id": BASE_ID, "revision": BASE_REVISION},
        "export": {"directory": str(EXPORT_ROOT), "quantization": "Q6_K", "runtime_model_replaced": False},
        "disk_free_gib": round(shutil.disk_usage(ROOT).free / 1024**3, 2),
    }
    subprocess.run(
        [str(QUANTIZER), _windows_path(F16_GGUF), _windows_path(Q6_GGUF), "Q6_K"],
        check=True,
    )
    return _finish_export(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explicit PEva V4 test GGUF export.")
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--resume-quantization", action="store_true")
    args = parser.parse_args(argv)
    adapter = args.adapter.expanduser().resolve()
    try:
        if args.dry_run:
            result = preflight(adapter)
        elif args.resume_quantization:
            result = resume_quantization(adapter)
        else:
            result = export(adapter)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
