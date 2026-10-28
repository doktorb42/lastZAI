"""One guarded, non-production QLoRA run for the PEva Agency pilot."""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from trening.agency_dataset_builder import MANIFEST_PATH, SEED, TRAIN_PATH, VALIDATION_PATH, build


ROOT = Path(__file__).resolve().parents[1]
RUN_NAME = "peva-qwen2.5-7b-agency-pilot-v1"
RUNS_DIR = ROOT / "data" / "training" / "runs"
RUN_DIR = RUNS_DIR / RUN_NAME
BASE_BENCHMARK = ROOT / "data" / "training" / "agency" / "benchmarks" / "peva_agency_v0_base.json"
BASE_ID = "Qwen/Qwen2.5-7B-Instruct"
BASE_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
TARGET_MODULES = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
CONFIG = {
    "base_model": {"id": BASE_ID, "revision": BASE_REVISION, "format": "transformers", "gguf_training_forbidden": True},
    "qlora": {"load_in_4bit": True, "quant_type": "nf4", "double_quant": True, "lora_r": 8, "lora_alpha": 16, "lora_dropout": 0.05},
    "training": {"max_seq_length": 1024, "per_device_train_batch_size": 1, "gradient_accumulation_steps": 16, "num_train_epochs": 1, "learning_rate": 5e-5, "optimizer": "adamw_8bit", "seed": SEED, "gradient_checkpointing": True},
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_completed_run(path: Path) -> bool:
    """Return whether a run directory contains a completed pilot report."""
    metrics = path / "metrics.json"
    if not metrics.is_file():
        return False
    try:
        return json.loads(metrics.read_text(encoding="utf-8")).get("status") == "completed"
    except (OSError, json.JSONDecodeError):
        return False


def _planned_run_dir() -> tuple[Path | None, Path | None]:
    """Choose a fresh output directory without discarding an interrupted attempt."""
    existing = sorted(path for path in RUNS_DIR.glob(f"{RUN_NAME}*") if path.is_dir()) if RUNS_DIR.is_dir() else []
    completed = next((path for path in existing if _is_completed_run(path)), None)
    if completed is not None:
        return None, None
    if not RUN_DIR.exists():
        return RUN_DIR, None
    retry = 1
    while True:
        candidate = RUNS_DIR / f"{RUN_NAME}-retry-{retry}"
        if not candidate.exists():
            return candidate, RUN_DIR
        retry += 1


def preflight() -> dict[str, Any]:
    dataset = build(write=False)
    benchmark_status = "missing"
    if BASE_BENCHMARK.is_file():
        try:
            benchmark_status = str(json.loads(BASE_BENCHMARK.read_text(encoding="utf-8")).get("status") or "unknown")
        except (OSError, json.JSONDecodeError):
            benchmark_status = "invalid"
    run_dir, previous_incomplete_run = _planned_run_dir()
    result: dict[str, Any] = {"generated_at_utc": now(), "platform": platform.platform(), "python": sys.version.split()[0], "dataset_pilot_gate": dataset["pilot_gate"], "base_benchmark_status": benchmark_status, "base_model": CONFIG["base_model"], "logical_run_name": RUN_NAME}
    if run_dir is None:
        result.update(run_name=RUN_NAME, ready=False, reason=f"Refusing to overwrite completed pilot run: {RUN_DIR}")
        return result
    result.update(run_name=run_dir.name, run_directory=str(run_dir))
    if previous_incomplete_run is not None:
        result["previous_incomplete_run"] = str(previous_incomplete_run)
    if platform.system() != "Linux":
        result.update(ready=False, reason="Actual QLoRA must run in the isolated WSL/Linux environment, not Windows Python.")
        return result
    try:
        import torch  # type: ignore[import-not-found]
        import bitsandbytes  # type: ignore[import-not-found]
        import peft  # type: ignore[import-not-found]
        import transformers  # type: ignore[import-not-found]
    except ImportError as exc:
        result.update(ready=False, reason=f"Missing QLoRA dependency: {exc.name}")
        return result
    cuda = bool(torch.cuda.is_available())
    free, total = torch.cuda.mem_get_info(0) if cuda else (0, 0)
    result.update(torch=torch.__version__, transformers=transformers.__version__, peft=getattr(peft, "__version__", "unknown"), bitsandbytes=getattr(bitsandbytes, "__version__", "unknown"), cuda_available=cuda, cuda_runtime=torch.version.cuda, gpu=torch.cuda.get_device_name(0) if cuda else None, bf16=bool(torch.cuda.is_bf16_supported()) if cuda else False, free_vram_gib=round(free / 1024**3, 2), total_vram_gib=round(total / 1024**3, 2))
    if not dataset["pilot_gate"]["passed"]:
        result.update(ready=False, reason="Agency pilot dataset gate failed.")
    elif benchmark_status != "completed":
        result.update(ready=False, reason="Pinned 7B base Agency benchmark is not completed; run the documented WSL/.venv-agency base benchmark first.")
    elif not cuda:
        result.update(ready=False, reason="CUDA is unavailable in the Linux training interpreter.")
    else:
        result["ready"] = True
    return result


def _rows(path: Path) -> list[dict[str, Any]]:
    return [dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _chat_template_ids(tokenizer: Any, messages: list[Mapping[str, str]], *, add_generation_prompt: bool) -> list[int]:
    """Normalize token IDs across list and Transformers BatchEncoding returns."""
    rendered = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=add_generation_prompt)
    if isinstance(rendered, Mapping):
        if "input_ids" not in rendered:
            raise RuntimeError("Agency chat template did not return input_ids.")
        rendered = rendered["input_ids"]
    if hasattr(rendered, "tolist"):
        rendered = rendered.tolist()
    if isinstance(rendered, tuple):
        rendered = list(rendered)
    if isinstance(rendered, list) and len(rendered) == 1 and isinstance(rendered[0], (list, tuple)):
        rendered = list(rendered[0])
    if not isinstance(rendered, list) or not all(isinstance(token, int) for token in rendered):
        raise RuntimeError("Agency chat template returned invalid input_ids.")
    return rendered


def _validate_chat_template_rows(tokenizer: Any, rows: list[Mapping[str, Any]]) -> dict[str, int]:
    """Validate every training row before any output directory is created."""
    invalid: list[str] = []
    max_tokens = 0
    for row in rows:
        messages = row.get("messages")
        record_id = str(row.get("id") or "<missing-id>")
        if not isinstance(messages, list) or len(messages) < 2:
            invalid.append(f"{record_id}: missing chat messages")
            continue
        try:
            full = _chat_template_ids(tokenizer, messages, add_generation_prompt=False)
            prompt = _chat_template_ids(tokenizer, messages[:-1], add_generation_prompt=True)
        except RuntimeError as exc:
            invalid.append(f"{record_id}: {exc}")
            continue
        max_tokens = max(max_tokens, len(full))
        if len(full) <= len(prompt) or len(full) > CONFIG["training"]["max_seq_length"]:
            invalid.append(f"{record_id}: full={len(full)}, prompt={len(prompt)}, limit={CONFIG['training']['max_seq_length']}")
    if invalid:
        preview = "; ".join(invalid[:5])
        suffix = "" if len(invalid) <= 5 else f"; plus {len(invalid) - 5} more"
        raise RuntimeError(f"Invalid or oversized Agency chat-template sample(s): {preview}{suffix}")
    return {"records": len(rows), "max_tokens": max_tokens}


def _assistant_batch(tokenizer: Any, messages: list[Mapping[str, str]], device: Any) -> dict[str, Any]:
    import torch  # type: ignore[import-not-found]
    full = _chat_template_ids(tokenizer, messages, add_generation_prompt=False)
    prompt = _chat_template_ids(tokenizer, messages[:-1], add_generation_prompt=True)
    if len(full) <= len(prompt) or len(full) > CONFIG["training"]["max_seq_length"]:
        raise RuntimeError("Invalid or oversized Agency chat-template sample.")
    input_ids = torch.tensor([full], dtype=torch.long, device=device)
    labels = input_ids.clone()
    labels[:, :len(prompt)] = -100
    return {"input_ids": input_ids, "attention_mask": torch.ones_like(input_ids), "labels": labels}


def _save_evidence(preflight_report: Mapping[str, Any], run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "training_config.json").write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "environment_gpu.json").write_text(json.dumps(preflight_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for source in (MANIFEST_PATH, TRAIN_PATH, VALIDATION_PATH, ROOT / "agency_dataset_audit.py", ROOT / "agency_dataset_builder.py", ROOT / "agency_core.py", ROOT / "agency_types.py", ROOT / "peva_self.py", ROOT / "config" / "peva_self.json"):
        if source.is_file():
            target = run_dir / "source_snapshot" / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def train() -> dict[str, Any]:
    readiness = preflight()
    if not readiness.get("ready"):
        raise RuntimeError(str(readiness.get("reason") or "QLoRA preflight failed."))
    import torch  # type: ignore[import-not-found]
    import bitsandbytes as bnb  # type: ignore[import-not-found]
    from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training  # type: ignore[import-not-found]
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # type: ignore[import-not-found]

    random.seed(SEED)
    torch.manual_seed(int.from_bytes(SEED.encode("utf-8"), "little") % (2**31 - 1))
    torch.cuda.manual_seed_all(int.from_bytes(SEED.encode("utf-8"), "little") % (2**31 - 1))
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
    tokenizer = AutoTokenizer.from_pretrained(BASE_ID, revision=BASE_REVISION, local_files_only=True, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    rows = _rows(TRAIN_PATH)
    chat_template_validation = _validate_chat_template_rows(tokenizer, rows)
    run_dir = Path(str(readiness["run_directory"]))
    _save_evidence({**readiness, "chat_template_validation": chat_template_validation}, run_dir)
    model = AutoModelForCausalLM.from_pretrained(BASE_ID, revision=BASE_REVISION, local_files_only=True, quantization_config=quantization, dtype=dtype, device_map={"": 0})
    model.config.use_cache = False
    found = {name.rsplit(".", 1)[-1] for name, _module in model.named_modules()}
    missing = [name for name in TARGET_MODULES if name not in found]
    if missing:
        raise RuntimeError(f"Required LoRA target modules are absent: {missing}")
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model.gradient_checkpointing_enable()
    model = get_peft_model(model, LoraConfig(task_type=TaskType.CAUSAL_LM, r=8, lora_alpha=16, lora_dropout=0.05, bias="none", target_modules=list(TARGET_MODULES)))
    optimizer = bnb.optim.AdamW8bit((parameter for parameter in model.parameters() if parameter.requires_grad), lr=5e-5)
    losses: list[float] = []
    accumulation = 16
    model.train()
    optimizer.zero_grad(set_to_none=True)
    for step, row in enumerate(rows, start=1):
        batch = _assistant_batch(tokenizer, row["messages"], torch.device("cuda:0"))
        with torch.autocast(device_type="cuda", dtype=dtype):
            loss = model(**batch).loss / accumulation
        loss.backward()
        losses.append(float(loss.detach().cpu()) * accumulation)
        if step % accumulation == 0 or step == len(rows):
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
    adapter = run_dir / "adapter"
    model.save_pretrained(adapter)
    tokenizer.save_pretrained(adapter)
    free, total = torch.cuda.mem_get_info(0)
    report = {"status": "completed", "run_name": run_dir.name, "logical_run_name": RUN_NAME, "completed_at_utc": now(), "adapter": str(adapter), "training": {"samples": len(rows), "epochs": 1, "micro_steps": len(rows), "optimizer_steps": (len(rows) + accumulation - 1) // accumulation, "loss_first": losses[0], "loss_last": losses[-1], "loss_min": min(losses), "loss_max": max(losses), "compute_dtype": str(dtype).replace("torch.", ""), "optimizer": "adamw_8bit", "chat_template_validation": chat_template_validation}, "gpu_after_gib": {"free": round(free / 1024**3, 2), "total": round(total / 1024**3, 2)}, "runtime_model_replaced": False, "gguf_exported": False}
    (run_dir / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded PEva Agency QLoRA pilot runner.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = preflight() if args.dry_run else train()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ready", report.get("status") == "completed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
