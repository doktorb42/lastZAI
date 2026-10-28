#!/usr/bin/env python3
"""Safely gated QLoRA trainer for PEva Chat V1.

This is intentionally separate from frozen Action V1.  It cannot start unless
``--run-full`` is explicit *and* ``chat_v1_readiness.py`` proves the approved,
player/thread-disjoint dataset is ready.  It never reads the protected test
split during training and never imports the Last Z runtime, ADB, or BlueStacks.
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
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from chat_quality import validate_player_ready_output
from chat_v1_readiness import DEFAULT_CONFIG, ReadinessError, evaluate_readiness
from dataset_common import DatasetError, read_jsonl, safe_within_training, sha256_file, write_json
from lastz_model_benchmark.conversation_system.lastz_conversation.contracts import (
    ContractError,
    render_chat_messages,
    validate_task_chat_input,
)


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
ADAPTERS_DIR = TRAINING_ROOT / "outputs" / "adapters"
REPORTS_DIR = TRAINING_ROOT / "outputs" / "reports"
_RUN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")


class TrainingGateError(RuntimeError):
    """A Chat V1 training prerequisite did not hold."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrainingGateError(f"Chat V1 configuration not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TrainingGateError(f"Chat V1 config must remain JSON-compatible YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise TrainingGateError("Chat V1 config must be an object")
    return value


def resolve_training_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise TrainingGateError(f"{label} must be a non-empty path")
    path = Path(value)
    return safe_within_training(path if path.is_absolute() else TRAINING_ROOT / path)


def _safe_run_name(value: str) -> str:
    if not _RUN_NAME_RE.fullmatch(value):
        raise TrainingGateError("run name must use only letters, digits, dot, underscore, and hyphen")
    return value


def _sha256_directory(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file():
            digest.update(str(child.relative_to(path)).replace("\\", "/").encode("utf-8"))
            digest.update(b"\0")
            with child.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def _assert_prompt_completion_rows(rows: list[dict[str, Any]], label: str) -> None:
    for index, row in enumerate(rows, 1):
        prefix = f"{label} row {index}"
        prompt = row.get("prompt")
        completion = row.get("completion")
        if not isinstance(prompt, list) or len(prompt) != 2:
            raise TrainingGateError(f"{prefix} must have an exact two-message prompt")
        if not isinstance(completion, list) or len(completion) != 1:
            raise TrainingGateError(f"{prefix} must have exactly one completion")
        if [message.get("role") if isinstance(message, Mapping) else None for message in prompt] != ["system", "user"]:
            raise TrainingGateError(f"{prefix} prompt roles must be system,user")
        assistant = completion[0]
        if not isinstance(assistant, Mapping) or assistant.get("role") != "assistant" or not isinstance(assistant.get("content"), str):
            raise TrainingGateError(f"{prefix} completion must be player-ready assistant text")
        user_content = prompt[1].get("content") if isinstance(prompt[1], Mapping) else None
        if not isinstance(user_content, str):
            raise TrainingGateError(f"{prefix} user prompt content is missing")
        _prefix, separator, encoded = user_content.partition("\n")
        if _prefix != "[PEVA TASK_CHAT INPUT]" or not separator:
            raise TrainingGateError(f"{prefix} lacks the exact canonical TASK_CHAT user prompt")
        try:
            task_input = json.loads(encoded)
            validated = validate_task_chat_input(task_input)
        except (json.JSONDecodeError, ContractError) as exc:
            raise TrainingGateError(f"{prefix} has invalid canonical TASK_CHAT input: {exc}") from exc
        if task_input != validated or prompt != render_chat_messages(validated["context"]):
            raise TrainingGateError(f"{prefix} does not match the exact V2 TASK_CHAT renderer")
        verdict = validate_player_ready_output(
            assistant["content"],
            {"input": validated["context"], "grounding": validated["context"]},
            apostrophe_policy="reject",
        )
        if not verdict.get("valid"):
            raise TrainingGateError(f"{prefix} completion fails player-ready gate: {verdict.get('errors')}")
        if row.get("split") != label:
            raise TrainingGateError(f"{prefix} has incorrect split marker")


def _percentile(values: list[int], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 2)


def _token_count(tokenizer: Any, messages: list[dict[str, Any]]) -> int:
    """Count the exact rendered conversation without allowing truncation."""

    try:
        tokens = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=False,
            truncation=False,
        )
        if isinstance(tokens, list):
            return len(tokens)
        # Some tokenizers return a tensor-like value when configured by an
        # integration.  It must still represent exactly one untruncated row.
        if hasattr(tokens, "shape"):
            shape = tuple(int(value) for value in tokens.shape)
            return int(shape[-1]) if shape else 0
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
        encoded = tokenizer(rendered, truncation=False, add_special_tokens=False)
        ids = encoded["input_ids"]
        return len(ids[0]) if ids and isinstance(ids[0], list) else len(ids)
    except Exception as exc:  # pragma: no cover - exact tokenizer API varies by installed stack
        raise TrainingGateError(f"unable to perform non-truncating tokenizer preflight: {exc}") from exc


def _assert_token_lengths(tokenizer: Any, rows: list[dict[str, Any]], label: str, maximum: int) -> dict[str, Any]:
    """Refuse any canonical prompt+completion that would be silently clipped."""

    lengths: list[int] = []
    over_limit: list[str] = []
    for index, row in enumerate(rows, 1):
        messages = row.get("messages")
        if not isinstance(messages, list):
            raise TrainingGateError(f"{label} row {index} has no rendered messages for token preflight")
        length = _token_count(tokenizer, messages)
        lengths.append(length)
        if length > maximum:
            over_limit.append(f"{row.get('record_id', f'{label}:{index}')}={length}")
    if over_limit:
        preview = ", ".join(over_limit[:12])
        suffix = "" if len(over_limit) <= 12 else f" (+{len(over_limit) - 12} more)"
        raise TrainingGateError(
            f"canonical TASK_CHAT prompt+completion exceeds max_seq_length={maximum}; "
            f"refusing silent truncation: {preview}{suffix}"
        )
    return {
        "rows": len(lengths),
        "min": min(lengths) if lengths else None,
        "max": max(lengths) if lengths else None,
        "p50": _percentile(lengths, 0.50),
        "p95": _percentile(lengths, 0.95),
        "max_seq_length": maximum,
        "all_rows_fit_without_truncation": True,
    }


def _require_cuda(torch: Any) -> dict[str, Any]:
    if not torch.cuda.is_available():
        raise TrainingGateError("CUDA is unavailable; CPU fallback is refused")
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


def _seed_everything(seed: int, torch: Any) -> None:
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np  # type: ignore
        np.random.seed(seed)
    except ImportError:
        pass
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def _package_snapshot() -> dict[str, str | None]:
    packages = ("torch", "unsloth", "unsloth-zoo", "transformers", "trl", "peft", "accelerate", "datasets", "bitsandbytes")
    result: dict[str, str | None] = {}
    for package in packages:
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def _nvidia_snapshot() -> str | None:
    try:
        import subprocess
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return completed.stdout.strip() or completed.stderr.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _construct_sft_config(SFTConfig: Any, config: Mapping[str, Any], output_dir: Path, use_bf16: bool) -> Any:
    training = config["training"]
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
        "optim": str(training.get("optimizer", "adamw_8bit")),
        "bf16": use_bf16,
        "fp16": not use_bf16,
        "packing": bool(training["packing"]),
        "completion_only_loss": bool(training["completion_only_loss"]),
        "eos_token": "<|im_end|>",
    }
    values["eval_strategy" if "eval_strategy" in supported else "evaluation_strategy"] = str(training["evaluation_strategy"])
    if "max_length" in supported:
        values["max_length"] = int(config["model"]["max_seq_length"])
    elif "max_seq_length" in supported:
        values["max_seq_length"] = int(config["model"]["max_seq_length"])
    return SFTConfig(**{key: value for key, value in values.items() if key in supported})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-full", action="store_true", help="Required explicit acknowledgement before any Chat V1 training can start.")
    parser.add_argument("--dry-run", action="store_true", help="Check all readiness/data gates without importing training packages or writing an adapter.")
    parser.add_argument("--run-name")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--model-path", type=Path, help="Optional local HF base below training/models/; no GGUF or remote fallback.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.run_full and not args.dry_run:
        raise TrainingGateError("refusing to train Chat V1 without explicit --run-full")
    config_path = safe_within_training(args.config if args.config.is_absolute() else TRAINING_ROOT / args.config)
    config = load_config(config_path)
    readiness = evaluate_readiness(config)
    if not readiness.get("ready"):
        raise TrainingGateError("Chat V1 readiness failed: " + "; ".join(str(item) for item in readiness.get("errors", [])))
    dataset = config.get("dataset")
    if not isinstance(dataset, Mapping):
        raise TrainingGateError("config.dataset is required")
    train_path = resolve_training_path(dataset.get("train_path"), "dataset.train_path")
    validation_path = resolve_training_path(dataset.get("validation_path"), "dataset.validation_path")
    test_path = resolve_training_path(dataset.get("test_path"), "dataset.test_path")
    train_rows, validation_rows = read_jsonl(train_path), read_jsonl(validation_path)
    # The protected test split is verified by readiness hashes, but it is not
    # opened, parsed, tokenized, or otherwise exposed to the trainer process.
    # This is stronger than merely omitting it from SFTTrainer.
    protected_test_rows = readiness.get("counts", {}).get("split", {}).get("test") if isinstance(readiness.get("counts"), Mapping) else None
    protected_test_hash = readiness.get("hashes", {}).get("test") if isinstance(readiness.get("hashes"), Mapping) else None
    _assert_prompt_completion_rows(train_rows, "train")
    _assert_prompt_completion_rows(validation_rows, "validation")
    if args.dry_run:
        print(json.dumps({"status": "dry_run_ready", "readiness": readiness, "train_rows": len(train_rows), "validation_rows": len(validation_rows), "protected_test_rows": protected_test_rows, "guarantees": ["no_model_loaded", "no_training_started", "protected_test_not_opened_or_used_by_training"]}, ensure_ascii=False, indent=2))
        return 0
    model = config.get("model")
    lora = config.get("lora")
    if not isinstance(model, Mapping) or not isinstance(lora, Mapping) or not isinstance(config.get("training"), Mapping):
        raise TrainingGateError("config must contain model, lora, and training objects")
    run_name = _safe_run_name(args.run_name or f"{config.get('experiment', 'peva-chat-v1')}-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    if args.output_dir:
        output_dir = safe_within_training(args.output_dir if args.output_dir.is_absolute() else TRAINING_ROOT / args.output_dir)
    else:
        output_dir = ADAPTERS_DIR / run_name
    try:
        output_dir.resolve().relative_to(ADAPTERS_DIR.resolve())
    except ValueError as exc:
        raise TrainingGateError(f"output directory must stay below {ADAPTERS_DIR}") from exc
    if output_dir.exists() and any(output_dir.iterdir()):
        raise TrainingGateError(
            f"refusing to overwrite non-empty Chat V1 adapter directory: {output_dir}; choose a new --run-name/output-dir"
        )
    report_path = REPORTS_DIR / f"{run_name}.json"
    report: dict[str, Any] = {
        "started_at": utc_now(),
        "status": "running",
        "run_id": run_name,
        "config_path": str(config_path),
        "output_dir": str(output_dir),
        "readiness": readiness,
        "dataset": {
            "train": {"path": str(train_path), "sha256": sha256_file(train_path), "rows": len(train_rows)},
            "validation": {"path": str(validation_path), "sha256": sha256_file(validation_path), "rows": len(validation_rows)},
            "protected_test": {"path": str(test_path), "sha256": protected_test_hash, "rows": protected_test_rows, "opened_or_used_by_training": False},
        },
        "packages": _package_snapshot(),
        "host": {"platform": platform.platform(), "python": sys.version, "nvidia_smi_before": _nvidia_snapshot()},
    }
    try:
        import torch
        from datasets import Dataset
        from trl import SFTConfig, SFTTrainer
        from unsloth import FastLanguageModel

        cuda = _require_cuda(torch)
        _seed_everything(int(config["training"]["seed"]), torch)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        local_model = None
        if args.model_path:
            local_model = safe_within_training(args.model_path if args.model_path.is_absolute() else TRAINING_ROOT / args.model_path)
            models_root = (TRAINING_ROOT / "models").resolve()
            try:
                local_model.relative_to(models_root)
            except ValueError as exc:
                raise TrainingGateError("--model-path must remain under training/models") from exc
            if local_model.suffix.casefold() == ".gguf":
                raise TrainingGateError("GGUF is an inference artifact, not a Chat V1 QLoRA training input")
        load_args: dict[str, Any] = {
            "model_name": str(local_model) if local_model else str(model["training_model_id"]),
            "max_seq_length": int(model["max_seq_length"]),
            "dtype": None,
            "load_in_4bit": bool(model["load_in_4bit"]),
            "device_map": {"": 0},
        }
        if local_model:
            load_args["local_files_only"] = True
        else:
            load_args["revision"] = str(model["training_revision"])
        loaded_model, tokenizer = FastLanguageModel.from_pretrained(**load_args)
        max_seq_length = int(model["max_seq_length"])
        token_preflight = {
            "train": _assert_token_lengths(tokenizer, train_rows, "train", max_seq_length),
            "validation": _assert_token_lengths(tokenizer, validation_rows, "validation", max_seq_length),
        }
        loaded_model = FastLanguageModel.get_peft_model(
            loaded_model,
            r=int(lora["r"]),
            target_modules=list(lora["target_modules"]),
            lora_alpha=int(lora["alpha"]),
            lora_dropout=float(lora["dropout"]),
            bias=str(lora["bias"]),
            use_gradient_checkpointing=str(lora["use_gradient_checkpointing"]),
            random_state=int(config["training"]["seed"]),
            use_rslora=bool(lora.get("use_rslora", False)),
            loftq_config=None,
        )
        trainer_args: dict[str, Any] = {
            "model": loaded_model,
            "args": _construct_sft_config(SFTConfig, config, output_dir, bool(cuda["bf16_supported"])),
            "train_dataset": Dataset.from_list(train_rows),
            "eval_dataset": Dataset.from_list(validation_rows),
        }
        signature = set(inspect.signature(SFTTrainer).parameters)
        trainer_args["processing_class" if "processing_class" in signature else "tokenizer"] = tokenizer
        trainer = SFTTrainer(**trainer_args)
        started = time.perf_counter()
        result = trainer.train()
        elapsed = time.perf_counter() - started
        trainer.save_model(str(output_dir))
        tokenizer.save_pretrained(str(output_dir))
        (output_dir / "peva_chat_v1_training_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report.update(
            status="passed",
            finished_at=utc_now(),
            cuda=cuda,
            training_metrics=getattr(result, "metrics", {}),
            trainer_log_history=trainer.state.log_history,
            adapter_sha256=_sha256_directory(output_dir),
            token_length_preflight=token_preflight,
            memory={
                "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
                "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
            },
            summary={"elapsed_seconds": round(elapsed, 3), "global_step": trainer.state.global_step, "protected_test_not_opened_or_used_by_training": True},
        )
    except Exception as exc:
        message = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        report.update(status="oom" if "out of memory" in message.lower() else "failed", finished_at=utc_now(), error=message, traceback=traceback.format_exc())
        raise
    finally:
        report.setdefault("finished_at", utc_now())
        report["host"]["nvidia_smi_after"] = _nvidia_snapshot()
        write_json(report_path, report)
    print(f"[OK] Chat V1 training report: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DatasetError, ReadinessError, TrainingGateError, ValueError) as exc:
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        raise SystemExit(2)

