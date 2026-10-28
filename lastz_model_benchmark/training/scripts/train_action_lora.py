#!/usr/bin/env python3
"""Train Last Z Action V1 with QLoRA after its mandatory data-quality gate.

This program is deliberately unable to fall back to CPU.  ``--smoke`` runs a
real ten-step CUDA training pass; ``--run-full`` is required for the actual
two-epoch experiment.  It neither imports the game application nor interacts
with ADB/BlueStacks.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
import platform
import random
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
DEFAULT_CONFIG = TRAINING_ROOT / "configs" / "action_v1.yaml"
REPORTS_DIR = TRAINING_ROOT / "outputs" / "reports"
ADAPTERS_DIR = TRAINING_ROOT / "outputs" / "adapters"
SAFE_RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")


class TrainingGateError(RuntimeError):
    """A deterministic prerequisite was not met; training must not start."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json_compatible_yaml(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrainingGateError(f"configuration not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TrainingGateError(
            f"{path} must remain JSON-compatible YAML for the reproducible pipeline: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise TrainingGateError(f"configuration must be an object: {path}")
    return value


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else TRAINING_ROOT / path


def resolve_training_file(value: Path, label: str) -> Path:
    candidate = (value if value.is_absolute() else TRAINING_ROOT / value).resolve()
    root = TRAINING_ROOT.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise TrainingGateError(f"{label} must remain below {root}; got {candidate}") from exc
    return candidate


def safe_run_name(value: str) -> str:
    if not SAFE_RUN_NAME.fullmatch(value):
        raise TrainingGateError(
            "--run-name may contain only letters, digits, '.', '_' and '-' and must not begin with punctuation"
        )
    return value


def resolve_local_model_path(value: Path) -> Path:
    """Validate an optional local *base* checkpoint kept in this bundle.

    The explicit check prevents an accidental adapter directory, GGUF, or an
    arbitrary path outside ``training/`` from becoming an undocumented model
    source.  It also makes an offline rerun deterministic after
    ``select_model.py --download``.
    """

    candidate = (value if value.is_absolute() else TRAINING_ROOT / value).resolve()
    training_root = TRAINING_ROOT.resolve()
    try:
        candidate.relative_to(training_root)
    except ValueError as exc:
        raise TrainingGateError(
            f"--model-path must remain below {training_root}; got {candidate}"
        ) from exc
    if not candidate.is_dir():
        raise TrainingGateError(f"--model-path is not a directory: {candidate}")
    required = ("config.json", "tokenizer_config.json")
    missing = [name for name in required if not (candidate / name).is_file()]
    if missing:
        raise TrainingGateError(
            "--model-path is not a complete Transformers base checkpoint; missing "
            + ", ".join(missing)
        )
    if not list(candidate.glob("*.safetensors")):
        raise TrainingGateError("--model-path has no local safetensors weights")
    if any(path.is_file() for path in candidate.rglob("*.gguf")):
        raise TrainingGateError("--model-path contains GGUF; QLoRA training requires Transformers weights")
    return candidate


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gate_path_under_training(raw_path: object) -> Path | None:
    """Map a gate path from Windows or WSL to the same bundle-relative path.

    The data gate was intentionally created on Windows while QLoRA runs in
    WSL.  Absolute paths are therefore platform-specific, but their suffix
    below ``training/`` is not.  The caller still verifies the immutable file
    hashes immediately afterwards, so this compatibility mapping cannot
    substitute a different processed dataset.
    """

    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    normalized = raw_path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    training_indexes = [index for index, part in enumerate(parts) if part.casefold() == "training"]
    if training_indexes:
        relative_parts = parts[training_indexes[-1] + 1 :]
        return TRAINING_ROOT.joinpath(*relative_parts).resolve()
    candidate = Path(raw_path)
    return (candidate if candidate.is_absolute() else TRAINING_ROOT / candidate).resolve()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise TrainingGateError(f"dataset not found: {path}")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrainingGateError(f"invalid JSONL at {path}:{number}: {exc}") from exc
        if not isinstance(record, dict):
            raise TrainingGateError(f"non-object record at {path}:{number}")
        rows.append(record)
    if not rows:
        raise TrainingGateError(f"dataset has no records: {path}")
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {})
    lines = [
        "# Last Z Action V1 training report",
        "",
        f"- Status: `{payload.get('status', 'unknown')}`",
        f"- Started: `{payload.get('started_at', 'unknown')}`",
        f"- Finished: `{payload.get('finished_at', 'unknown')}`",
        f"- Mode: `{payload.get('mode', 'unknown')}`",
        f"- Output: `{payload.get('output_dir', 'not created')}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key}: `{value}`")
    if payload.get("error"):
        lines.extend(["", "## Error", "", "```text", str(payload["error"]), "```"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def git_safe_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def packages_snapshot() -> dict[str, str | None]:
    return {
        name: git_safe_version(name)
        for name in (
            "torch",
            "unsloth",
            "unsloth-zoo",
            "transformers",
            "trl",
            "peft",
            "accelerate",
            "datasets",
            "bitsandbytes",
            "huggingface-hub",
        )
    }


def nvidia_snapshot() -> str | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,driver_version,memory.total,memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip() or result.stderr.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def assert_gate(config: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    dataset = config.get("dataset")
    if not isinstance(dataset, dict):
        raise TrainingGateError("config.dataset is required")
    train_path = resolve_path(str(dataset.get("train_path", "")))
    validation_path = resolve_path(str(dataset.get("validation_path", "")))
    gate_path = resolve_path(str(dataset.get("quality_gate_path", "")))
    if not gate_path.is_file():
        raise TrainingGateError(
            "missing action data quality gate; run validator, deduplicator, leakage check, and family split first"
        )
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TrainingGateError(f"invalid quality gate: {gate_path}: {exc}") from exc
    if gate.get("passed") is not True:
        raise TrainingGateError(f"data quality gate did not pass: {gate_path}")
    if gate.get("critical_errors"):
        raise TrainingGateError("data quality gate contains critical errors")
    # The splitter stores paths and hashes together, so a report cannot claim
    # that a different processed file was checked.  Accept the legacy flat
    # layout only to keep the checker useful if an older local run exists.
    hashes = gate.get("sha256", {})
    outputs = gate.get("outputs", {})
    if isinstance(outputs, dict):
        expected_train = outputs.get("train", {}).get("sha256") if isinstance(outputs.get("train"), dict) else None
        expected_validation = outputs.get("validation", {}).get("sha256") if isinstance(outputs.get("validation"), dict) else None
        expected_train_path = outputs.get("train", {}).get("path") if isinstance(outputs.get("train"), dict) else None
        expected_validation_path = outputs.get("validation", {}).get("path") if isinstance(outputs.get("validation"), dict) else None
        resolved_gate_train = gate_path_under_training(expected_train_path)
        resolved_gate_validation = gate_path_under_training(expected_validation_path)
        if resolved_gate_train and resolved_gate_train.resolve() != train_path.resolve():
            raise TrainingGateError("quality gate train path does not match the configured processed dataset")
        if resolved_gate_validation and resolved_gate_validation.resolve() != validation_path.resolve():
            raise TrainingGateError("quality gate validation path does not match the configured processed dataset")
    else:
        expected_train = hashes.get("train") if isinstance(hashes, dict) else None
        expected_validation = hashes.get("validation") if isinstance(hashes, dict) else None
    if not isinstance(expected_train, str) or not isinstance(expected_validation, str):
        raise TrainingGateError("quality gate has no train/validation hashes")
    actual_train = sha256(train_path)
    actual_validation = sha256(validation_path)
    if actual_train != expected_train or actual_validation != expected_validation:
        raise TrainingGateError(
            "processed data changed after validation/split; rerun the full Python quality pipeline"
        )
    return train_path, validation_path, gate


def assert_prompt_completion_rows(rows: list[dict[str, Any]], label: str) -> None:
    # Local pure contract: this is intentionally not the game executor and
    # cannot contact ADB.  The trainer repeats the schema check so a forged
    # quality-gate report cannot smuggle an arbitrary action into a completion.
    from lastz_action_contract import strict_selector_schema

    for index, row in enumerate(rows, 1):
        prompt = row.get("prompt")
        completion = row.get("completion")
        if not isinstance(prompt, list) or len(prompt) != 2:
            raise TrainingGateError(f"{label} row {index} must contain a two-message prompt")
        if not isinstance(completion, list) or len(completion) != 1:
            raise TrainingGateError(f"{label} row {index} must contain one completion message")
        system, user = prompt
        assistant = completion[0]
        if (
            not isinstance(system, dict)
            or system.get("role") != "system"
            or not isinstance(system.get("content"), str)
            or not isinstance(user, dict)
            or user.get("role") != "user"
            or not isinstance(user.get("content"), str)
            or not isinstance(assistant, dict)
            or assistant.get("role") != "assistant"
            or not isinstance(assistant.get("content"), str)
        ):
            raise TrainingGateError(f"{label} row {index} has malformed conversational messages")
        answer = assistant["content"]
        try:
            parsed = json.loads(answer)
        except json.JSONDecodeError as exc:
            raise TrainingGateError(f"{label} row {index} completion is not JSON: {exc}") from exc
        schema_valid, schema_reason = strict_selector_schema(parsed)
        if not schema_valid:
            raise TrainingGateError(
                f"{label} row {index} completion violates the selector schema: {schema_reason}"
            )
        if "text" in parsed or any(key.lower() in {"x", "y", "coordinate", "coordinates"} for key in parsed):
            raise TrainingGateError(f"{label} row {index} attempts to teach forbidden raw message/coordinates")


def seed_everything(seed: int, torch: Any) -> None:
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np  # type: ignore

        np.random.seed(seed)
    except ImportError:
        pass
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def require_cuda(torch: Any) -> dict[str, Any]:
    if not torch.cuda.is_available():
        raise TrainingGateError("CUDA is not available; refusing CPU fallback")
    device = torch.cuda.current_device()
    properties = torch.cuda.get_device_properties(device)
    return {
        "device_index": device,
        "name": torch.cuda.get_device_name(device),
        "total_memory_mib": round(properties.total_memory / 1024**2, 1),
        "compute_capability": f"{properties.major}.{properties.minor}",
        "torch_cuda": torch.version.cuda,
        "bf16_supported": bool(torch.cuda.is_bf16_supported()),
    }


def construct_sft_config(SFTConfig: Any, settings: dict[str, Any], output_dir: Path, smoke: bool, use_bf16: bool) -> Any:
    training = settings["training"]
    supported = set(inspect.signature(SFTConfig).parameters)
    values: dict[str, Any] = {
        "output_dir": str(output_dir),
        "per_device_train_batch_size": int(training["per_device_train_batch_size"]),
        "per_device_eval_batch_size": 1,
        "gradient_accumulation_steps": int(training["gradient_accumulation_steps"]),
        "learning_rate": float(training["learning_rate"]),
        "warmup_ratio": float(training["warmup_ratio"]),
        "num_train_epochs": float(training["num_train_epochs"]),
        "logging_steps": int(training["logging_steps"]),
        "save_strategy": str(training["save_strategy"]),
        "save_total_limit": 2,
        "seed": int(training["seed"]),
        "report_to": "none",
        "gradient_checkpointing": True,
        "optim": "adamw_8bit",
        "bf16": use_bf16,
        "fp16": not use_bf16,
        "packing": bool(training["packing"]),
        "completion_only_loss": bool(training["completion_only_loss"]),
        "eos_token": "<|im_end|>",
    }
    # Transformers and TRL renamed this property at different points.
    if "eval_strategy" in supported:
        values["eval_strategy"] = str(training["evaluation_strategy"])
    else:
        values["evaluation_strategy"] = str(training["evaluation_strategy"])
    if "max_length" in supported:
        values["max_length"] = int(settings["model"]["max_seq_length"])
    elif "max_seq_length" in supported:
        values["max_seq_length"] = int(settings["model"]["max_seq_length"])
    if smoke:
        values["max_steps"] = int(settings["safety"]["smoke_steps"])
        values["num_train_epochs"] = 1.0
        values["save_strategy"] = "no"
        values["eval_strategy" if "eval_strategy" in supported else "evaluation_strategy"] = "no"
    return SFTConfig(**{key: value for key, value in values.items() if key in supported})


def train(args: argparse.Namespace) -> dict[str, Any]:
    config_path = resolve_training_file(args.config, "--config")
    config = load_json_compatible_yaml(config_path)
    train_path, validation_path, gate = assert_gate(config)
    train_rows = read_jsonl(train_path)
    validation_rows = read_jsonl(validation_path)
    assert_prompt_completion_rows(train_rows, "train")
    assert_prompt_completion_rows(validation_rows, "validation")
    model_settings = config["model"]
    local_model_path = resolve_local_model_path(args.model_path) if args.model_path else None
    model_source = str(local_model_path) if local_model_path else str(model_settings["training_model_id"])
    mode = "smoke" if args.smoke else "full"
    run_id = safe_run_name(
        args.run_name or f"{config['experiment']}-{mode}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    if args.output_dir:
        requested_output = args.output_dir if args.output_dir.is_absolute() else TRAINING_ROOT / args.output_dir
        output_dir = requested_output.resolve()
        adapters_root = ADAPTERS_DIR.resolve()
        try:
            output_dir.relative_to(adapters_root)
        except ValueError as exc:
            raise TrainingGateError(
                f"--output-dir must remain below {adapters_root}; got {output_dir}"
            ) from exc
    else:
        output_dir = ADAPTERS_DIR / run_id
    # ``Path.with_suffix`` treats the `.5` in `qwen2.5` as an extension and
    # silently truncates reports.  Build explicit filenames instead.
    report_running = REPORTS_DIR / f"{run_id}.running.json"
    report_json = REPORTS_DIR / f"{run_id}.json"
    report_markdown = REPORTS_DIR / f"{run_id}.md"
    report: dict[str, Any] = {
        "started_at": utc_now(),
        "status": "running",
        "mode": mode,
        "run_id": run_id,
        "output_dir": str(output_dir),
        "config_path": str(config_path),
        "model_source": {
            "kind": "local_checkpoint" if local_model_path else "huggingface_repository",
            "value": model_source,
            "revision": None if local_model_path else str(model_settings["training_revision"]),
        },
        "config": config,
        "dataset": {
            "train_path": str(train_path),
            "validation_path": str(validation_path),
            "train_count": len(train_rows),
            "validation_count": len(validation_rows),
            "quality_gate": gate,
        },
        "packages": packages_snapshot(),
        "host": {"platform": platform.platform(), "python": sys.version, "nvidia_smi_before": nvidia_snapshot()},
        "summary": {},
    }
    write_json(report_running, report)
    if args.dry_run:
        report.update(
            status="dry_run",
            finished_at=utc_now(),
            summary={"would_train_rows": len(train_rows), "would_validate_rows": len(validation_rows)},
        )
        write_json(report_json, report)
        write_markdown(report_markdown, report)
        if report_running.exists():
            report_running.unlink()
        return report

    try:
        import torch
        # Unsloth patches the compatible Transformers/TRL stack at import
        # time.  Keep this ahead of TRL to preserve its Qwen integrations.
        from unsloth import FastLanguageModel
        from datasets import Dataset
        from trl import SFTConfig, SFTTrainer

        cuda = require_cuda(torch)
        seed_everything(int(config["training"]["seed"]), torch)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        memory_before = {
            "allocated_mib": round(torch.cuda.memory_allocated() / 1024**2, 2),
            "reserved_mib": round(torch.cuda.memory_reserved() / 1024**2, 2),
        }
        lora = config["lora"]
        load_arguments: dict[str, Any] = {
            "model_name": model_source,
            "max_seq_length": int(model_settings["max_seq_length"]),
            "dtype": None,
            "load_in_4bit": bool(model_settings["load_in_4bit"]),
        }
        if not local_model_path:
            load_arguments["revision"] = str(model_settings["training_revision"])
        else:
            # A reviewed local snapshot must never cause a silent network
            # fallback during a supposedly reproducible smoke/full run.
            load_arguments["local_files_only"] = True
        # Explicitly use the validated CUDA device.  If the 8 GB card cannot
        # hold this QLoRA setup, the run must report OOM instead of dispatching
        # layers to CPU.
        load_arguments["device_map"] = {"": 0}
        model, tokenizer = FastLanguageModel.from_pretrained(**load_arguments)
        model = FastLanguageModel.get_peft_model(
            model,
            r=int(lora["r"]),
            target_modules=list(lora["target_modules"]),
            lora_alpha=int(lora["alpha"]),
            lora_dropout=float(lora["dropout"]),
            bias=str(lora["bias"]),
            use_gradient_checkpointing=str(lora["use_gradient_checkpointing"]),
            random_state=int(config["training"]["seed"]),
            use_rslora=bool(lora["use_rslora"]),
            loftq_config=None,
        )
        sft_config = construct_sft_config(
            SFTConfig, config, output_dir, args.smoke, bool(cuda["bf16_supported"])
        )
        trainer_args = {
            "model": model,
            "args": sft_config,
            "train_dataset": Dataset.from_list(train_rows),
            "eval_dataset": Dataset.from_list(validation_rows),
        }
        supported_trainer = set(inspect.signature(SFTTrainer).parameters)
        if "processing_class" in supported_trainer:
            trainer_args["processing_class"] = tokenizer
        elif "tokenizer" in supported_trainer:
            trainer_args["tokenizer"] = tokenizer
        trainer = SFTTrainer(**trainer_args)
        started = time.perf_counter()
        result = trainer.train(resume_from_checkpoint=args.resume_from_checkpoint)
        elapsed = time.perf_counter() - started
        trainer.save_model(str(output_dir))
        tokenizer.save_pretrained(str(output_dir))
        (output_dir / "lastz_training_config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output_dir / "lastz_quality_gate.json").write_text(
            json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report.update(
            status="passed",
            finished_at=utc_now(),
            cuda=cuda,
            training_metrics=getattr(result, "metrics", {}),
            trainer_log_history=trainer.state.log_history,
            memory={
                "before": memory_before,
                "after": {
                    "allocated_mib": round(torch.cuda.memory_allocated() / 1024**2, 2),
                    "reserved_mib": round(torch.cuda.memory_reserved() / 1024**2, 2),
                },
                "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
                "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
            },
            summary={
                "elapsed_seconds": round(elapsed, 3),
                "train_rows": len(train_rows),
                "validation_rows": len(validation_rows),
                "cuda_device": cuda["name"],
                "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
                "global_step": trainer.state.global_step,
            },
        )
    except Exception as exc:  # report first; callers still receive nonzero status below
        message = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        report.update(
            status="oom" if "out of memory" in message.lower() else "failed",
            finished_at=utc_now(),
            error=message,
            traceback=traceback.format_exc(),
            summary={
                "next_action": (
                    "Keep batch size at 1; reduce max sequence length only after recording the OOM report."
                    if "out of memory" in message.lower()
                    else "Fix the reported CUDA/dependency/data gate failure; do not enable CPU fallback."
                )
            },
        )
        raise
    finally:
        report["host"]["nvidia_smi_after"] = nvidia_snapshot()
        if "finished_at" not in report:
            report["finished_at"] = utc_now()
        write_json(report_json, report)
        write_markdown(report_markdown, report)
        if report_running.exists():
            report_running.unlink()
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--smoke", action="store_true", help="Run exactly the configured 10-step CUDA smoke test.")
    choice.add_argument("--run-full", action="store_true", help="Run the configured full Action V1 experiment.")
    parser.add_argument("--run-name")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--model-path",
        type=Path,
        help="Optional downloaded Transformers base checkpoint below training/; avoids a second network fetch.",
    )
    parser.add_argument("--resume-from-checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="Check data gates without importing training packages.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = train(args)
    except TrainingGateError as exc:
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] {report['status']} report: {REPORTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
