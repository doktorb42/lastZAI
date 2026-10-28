#!/usr/bin/env python3
"""Guarded QLoRA runner for PEva Training Stage 1.

``--smoke`` is intentionally isolated from collected player data.  ``--run``
refuses to start unless the data-quality audit passes and an immutable release
exists.  Neither mode changes the PEva runtime, ActionClaimGuard, game client,
ADB state, or a production model selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from trening.training_dataset_audit import ROOT, audit


CONFIG_PATH = ROOT / "config" / "training" / "peva_qwen25_7b_v1.yaml"
BENCHMARK = ROOT / "benchmarks" / "peva_private_v1.json"
FINGERPRINT = ROOT / "benchmarks" / "peva_private_v1.sha256"
SMOKE_DATA = ROOT / "data" / "training" / "dev" / "peva_qwen7b_smoke_v1.jsonl"
RUN_ROOT = ROOT / "data" / "training" / "runs"
RELEASE_ROOT = ROOT / "data" / "training" / "releases" / "peva_private_v1_001"
BASE_ID = "Qwen/Qwen2.5-7B-Instruct"
BASE_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
TARGET_MODULES = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")

SMOKE_SAMPLES = (
    ("How is your day going?", "Pretty well, thanks. How about yours?"),
    ("What is a good way to prepare for an event?", "Check the event time, resources, and your alliance plan first."),
    ("I prefer defensive play.", "Got it — I’ll keep that preference in mind."),
    ("Can you explain why coordination matters?", "It helps everyone use marches and resources at the right time."),
)
SYSTEM = "You are PEva, a concise natural Last Z private-chat player. Never claim an unconfirmed game action."


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_frozen_benchmark() -> str:
    if not BENCHMARK.is_file() or not FINGERPRINT.is_file():
        raise RuntimeError("The PEva benchmark or its frozen SHA-256 file is missing.")
    expected = FINGERPRINT.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256(BENCHMARK)
    if expected != actual:
        raise RuntimeError(f"Benchmark SHA-256 mismatch: expected {expected}, got {actual}.")
    return actual


def load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("PyYAML is required in the isolated Linux training environment.") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise RuntimeError("Training configuration must be a YAML mapping.")
    base = data.get("base_model")
    if not isinstance(base, Mapping) or base.get("id") != BASE_ID or base.get("revision") != BASE_REVISION:
        raise RuntimeError("Configuration does not pin the required Qwen/Qwen2.5-7B-Instruct revision.")
    if base.get("gguf_training_forbidden") is not True:
        raise RuntimeError("Configuration must explicitly forbid GGUF training.")
    return dict(data)


def write_smoke_data(path: Path) -> list[dict[str, Any]]:
    """Write a declared non-release dev set used solely for the hardware smoke test."""

    rows = [
        {
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": reply},
            ],
            "metadata": {"scope": "smoke_only", "source": "static_dev", "release_eligible": False},
        }
        for prompt, reply in SMOKE_SAMPLES
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return rows


def read_release_data(path: Path) -> list[dict[str, Any]]:
    target = path / "reply_train.jsonl"
    if not target.is_file():
        raise RuntimeError(f"Required immutable release dataset is missing: {target}")
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if isinstance(item, Mapping) and isinstance(item.get("messages"), list):
            rows.append(dict(item))
    if not rows:
        raise RuntimeError(f"Immutable release has no usable reply rows: {target}")
    return rows


def preflight(mode: str) -> dict[str, Any]:
    report = audit()
    if platform.system() != "Linux":
        return {
            "ready": False,
            "reason": "Run actual QLoRA only in the isolated WSL/Linux environment.",
            "mode": mode,
            "audit_gate": report["training_gate"],
        }
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        return {"ready": False, "reason": "PyTorch is not installed in this interpreter.", "mode": mode, "audit_gate": report["training_gate"]}
    cuda = bool(torch.cuda.is_available())
    free, total = torch.cuda.mem_get_info(0) if cuda else (0, 0)
    result = {
        "ready": cuda,
        "mode": mode,
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "cuda_available": cuda,
        "gpu": torch.cuda.get_device_name(0) if cuda else None,
        "free_vram_gib": round(free / 1024**3, 2),
        "total_vram_gib": round(total / 1024**3, 2),
        "audit_gate": report["training_gate"],
        "full_qlora_permitted": bool(report["full_qlora_permitted"]),
        "release_exists": RELEASE_ROOT.is_dir(),
    }
    if mode == "run" and report["training_gate"] != "passed":
        result.update(ready=False, reason="Data-quality gate failed; full QLoRA is forbidden.")
    elif mode == "run" and not RELEASE_ROOT.is_dir():
        result.update(ready=False, reason="The required immutable data release does not exist.")
    elif not cuda:
        result.update(reason="CUDA is unavailable in this interpreter.")
    return result


def assistant_only_batch(tokenizer: Any, messages: list[Mapping[str, Any]], device: Any) -> dict[str, Any]:
    """Tokenize ChatML through the checkpoint's template and mask the prompt."""

    if len(messages) < 2 or str(messages[-1].get("role")) != "assistant":
        raise RuntimeError("Every training sample must end with an assistant message.")
    full_ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
    prompt_ids = tokenizer.apply_chat_template(messages[:-1], tokenize=True, add_generation_prompt=True)
    if not full_ids or len(full_ids) <= len(prompt_ids):
        raise RuntimeError("Chat template did not produce an assistant training span.")
    import torch  # type: ignore[import-not-found]

    input_ids = torch.tensor([full_ids], dtype=torch.long, device=device)
    attention_mask = torch.ones_like(input_ids)
    labels = input_ids.clone()
    labels[:, : len(prompt_ids)] = -100
    return {"input_ids": input_ids, "attention_mask": attention_mask, "labels": labels}


def train(mode: str, *, steps: int | None, cache_dir: Path, save_smoke_adapter: bool) -> dict[str, Any]:
    if platform.system() != "Linux":
        raise RuntimeError("Actual QLoRA is restricted to the isolated WSL/Linux environment.")
    config = load_yaml_config(CONFIG_PATH)
    initial_fingerprint = assert_frozen_benchmark()
    readiness = preflight(mode)
    if not readiness.get("ready"):
        raise RuntimeError(str(readiness.get("reason") or "QLoRA preflight failed."))
    import torch  # type: ignore[import-not-found]
    from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training  # type: ignore[import-not-found]
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # type: ignore[import-not-found]

    mode_config = dict(config["smoke"] if mode == "smoke" else config["full"])
    qlora = dict(config["qlora"])
    if mode == "smoke":
        rows = write_smoke_data(SMOKE_DATA)
    else:
        rows = read_release_data(RELEASE_ROOT)
    configured_steps = mode_config.get("max_steps")
    requested_steps = int(
        steps
        if steps is not None
        else configured_steps
        if configured_steps is not None
        else len(rows) * int(mode_config.get("num_train_epochs", 1))
    )
    if mode == "smoke" and not 20 <= requested_steps <= 30:
        raise RuntimeError("Smoke mode requires 20–30 micro-steps.")
    if mode == "run" and requested_steps <= 0:
        raise RuntimeError("Full mode requires a positive number of micro-steps.")

    cache_dir.mkdir(parents=True, exist_ok=True)
    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type=str(qlora["quant_type"]),
        bnb_4bit_use_double_quant=bool(qlora["double_quant"]),
        bnb_4bit_compute_dtype=compute_dtype,
    )
    tokenizer = AutoTokenizer.from_pretrained(BASE_ID, revision=BASE_REVISION, cache_dir=str(cache_dir), use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        BASE_ID,
        revision=BASE_REVISION,
        cache_dir=str(cache_dir),
        quantization_config=quantization,
        torch_dtype=compute_dtype,
        device_map={"": 0},
    )
    model.config.use_cache = False
    found_modules = {name.rsplit(".", 1)[-1] for name, _ in model.named_modules()}
    target_modules = [name for name in TARGET_MODULES if name in found_modules]
    missing_modules = [name for name in TARGET_MODULES if name not in target_modules]
    if missing_modules:
        raise RuntimeError(f"Refusing QLoRA: expected target modules are absent: {missing_modules}")
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model.gradient_checkpointing_enable()
    model = get_peft_model(
        model,
        LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=int(qlora["lora_r"]),
            lora_alpha=int(qlora["lora_alpha"]),
            lora_dropout=float(qlora["lora_dropout"]),
            bias="none",
            target_modules=target_modules,
        ),
    )
    trainable, total = model.get_nb_trainable_parameters()
    optimizer = torch.optim.AdamW((parameter for parameter in model.parameters() if parameter.requires_grad), lr=float(mode_config["learning_rate"]))
    accumulation = int(mode_config["gradient_accumulation_steps"])
    device = torch.device("cuda:0")
    losses: list[float] = []
    model.train()
    optimizer.zero_grad(set_to_none=True)
    for step in range(requested_steps):
        row = rows[step % len(rows)]
        messages = row.get("messages")
        if not isinstance(messages, list):
            raise RuntimeError("Training row has no messages list.")
        batch = assistant_only_batch(tokenizer, messages, device)
        with torch.autocast(device_type="cuda", dtype=compute_dtype):
            output = model(**batch)
            loss = output.loss / accumulation
        loss.backward()
        losses.append(float(loss.detach().cpu()) * accumulation)
        if (step + 1) % accumulation == 0 or step + 1 == requested_steps:
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)

    final_fingerprint = assert_frozen_benchmark()
    if final_fingerprint != initial_fingerprint:
        raise RuntimeError("Benchmark changed during QLoRA; training evidence is not valid.")
    run_name = f"peva-qwen25-7b-{mode}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output_dir = RUN_ROOT / run_name
    output_dir.mkdir(parents=True, exist_ok=False)
    adapter_path = None
    if mode == "smoke" and save_smoke_adapter:
        adapter_path = output_dir / "adapter"
        model.save_pretrained(adapter_path)
        tokenizer.save_pretrained(adapter_path)
    report = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "mode": mode,
        "base_model": {"id": BASE_ID, "revision": BASE_REVISION, "format": "transformers", "gguf_used_for_training": False},
        "dataset": {"path": str(SMOKE_DATA if mode == "smoke" else RELEASE_ROOT), "samples": len(rows), "release_eligible": mode == "run"},
        "qlora": {"load_in_4bit": True, "quant_type": qlora["quant_type"], "double_quant": bool(qlora["double_quant"]), "compute_dtype": str(compute_dtype).replace("torch.", ""), "target_modules": target_modules},
        "training": {"micro_steps": requested_steps, "gradient_accumulation_steps": accumulation, "optimizer_steps": (requested_steps + accumulation - 1) // accumulation, "loss_first": losses[0], "loss_last": losses[-1], "loss_min": min(losses), "loss_max": max(losses), "trainable_parameters": int(trainable), "total_parameters": int(total)},
        "benchmark_sha256_before": initial_fingerprint,
        "benchmark_sha256_after": final_fingerprint,
        "adapter_saved": str(adapter_path) if adapter_path else None,
        "production_model_replaced": False,
        "action_claim_guard_changed": False,
    }
    (output_dir / "run_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded PEva Qwen2.5-7B QLoRA runner.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--smoke", action="store_true", help="Run a 20–30-step isolated development-data QLoRA smoke test.")
    group.add_argument("--run", action="store_true", help="Run full QLoRA only after the data audit and immutable release pass.")
    group.add_argument("--dry-run", action="store_true", help="Report readiness only; do not import training libraries or load a model.")
    parser.add_argument("--steps", type=int, help="Micro-steps. Smoke is limited to 20–30.")
    parser.add_argument("--cache-dir", default=str(ROOT / "data" / "training" / "models" / "hf-cache"))
    parser.add_argument("--save-smoke-adapter", action="store_true", help="Save a non-production smoke adapter for diagnosis only.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    mode = "smoke" if args.smoke else "run"
    if args.dry_run:
        print(json.dumps(preflight("smoke"), ensure_ascii=False, indent=2))
        return 0
    try:
        result = train(mode, steps=args.steps, cache_dir=Path(args.cache_dir), save_smoke_adapter=bool(args.save_smoke_adapter))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
