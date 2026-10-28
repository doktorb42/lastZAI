#!/usr/bin/env python3
"""Run a deterministic, local-only Chat V1 holdout evaluation.

This evaluator is deliberately separate from the trainer.  It opens the
protected Chat V1 split *only after* an immutable adapter has been produced,
uses a local base checkpoint plus an optional local LoRA adapter, and never
imports the Last Z runtime, ADB, or BlueStacks.  It writes screened generation
results for human review; its automatic quality checks are evidence, not a
claim that a model is ready to send real messages autonomously.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from chat_quality import summarize_chat_quality_verdicts, validate_player_ready_output
from dataset_common import DatasetError, read_jsonl, safe_within_training, write_json
from lastz_model_benchmark.conversation_system.lastz_conversation.contracts import ContractError, validate_task_chat_input
from lastz_model_benchmark.conversation_system.lastz_conversation.contracts import render_chat_messages


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
MODELS_ROOT = TRAINING_ROOT / "models"
ADAPTERS_ROOT = TRAINING_ROOT / "outputs" / "adapters"
REPORTS_ROOT = TRAINING_ROOT / "outputs" / "reports"
EVALUATIONS_ROOT = TRAINING_ROOT / "outputs" / "evaluations"
DEFAULT_TEST = TRAINING_ROOT / "data" / "processed" / "chat_v1_test.jsonl"
DEFAULT_QUALITY_BENCHMARK = TRAINING_ROOT.parent / "benchmarks" / "cases" / "chat_v1_quality_cases.jsonl"

# Text promises about a possible move ("we could send") are normal player
# conversation.  Only a first-person claim of an already-executed game action
# is counted here, because PEva has no authority to perform an action.
_ACTION_CLAIM = re.compile(
    r"\b(?:i(?:'ve| have)\s+(?:already\s+)?|i\s+(?:already\s+)?)"
    r"(?:sent|moved|tapped|attacked|scouted|reinforced|joined|started|upgraded)\b",
    re.IGNORECASE,
)


class EvaluationError(RuntimeError):
    """A local-only holdout evaluation prerequisite did not hold."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_directory(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file():
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
            with child.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def require_within(path: Path, parent: Path, label: str) -> Path:
    resolved = safe_within_training(path if path.is_absolute() else TRAINING_ROOT / path)
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise EvaluationError(f"{label} must remain below {parent}") from exc
    return resolved


def _prompt_and_context(row: Mapping[str, Any]) -> tuple[list[dict[str, str]], Mapping[str, Any]]:
    prompt = row.get("prompt")
    if not isinstance(prompt, list) or len(prompt) != 2:
        raise EvaluationError(f"{row.get('record_id', '<unknown>')} does not contain an exact two-message prompt")
    rendered: list[dict[str, str]] = []
    for message in prompt:
        if not isinstance(message, Mapping) or not isinstance(message.get("role"), str) or not isinstance(message.get("content"), str):
            raise EvaluationError(f"{row.get('record_id', '<unknown>')} contains a malformed prompt message")
        rendered.append({"role": str(message["role"]), "content": str(message["content"])})
    user_text = rendered[1]["content"]
    marker, separator, encoded = user_text.partition("\n")
    if marker != "[PEVA TASK_CHAT INPUT]" or not separator:
        raise EvaluationError(f"{row.get('record_id', '<unknown>')} is not a canonical TASK_CHAT prompt")
    try:
        task = validate_task_chat_input(json.loads(encoded))
    except (json.JSONDecodeError, ContractError) as exc:
        raise EvaluationError(f"{row.get('record_id', '<unknown>')} has invalid TASK_CHAT input: {exc}") from exc
    return rendered, task["context"]


def generate_reply(model: Any, tokenizer: Any, prompt: list[dict[str, str]], *, max_new_tokens: int) -> str:
    try:
        rendered = tokenizer.apply_chat_template(prompt, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(rendered, return_tensors="pt")
        inputs = {key: value.to(model.device) for key, value in inputs.items()}
        generated = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            use_cache=True,
            pad_token_id=tokenizer.eos_token_id,
        )
        prompt_tokens = int(inputs["input_ids"].shape[-1])
        new_tokens = generated[0][prompt_tokens:]
        return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
    except Exception as exc:  # pragma: no cover - depends on CUDA/model stack
        raise EvaluationError(f"generation failed: {type(exc).__name__}: {exc}") from exc


def evaluation_summary(cases: list[Mapping[str, Any]]) -> dict[str, Any]:
    verdicts = [case["quality"] for case in cases if isinstance(case.get("quality"), Mapping)]
    quality = summarize_chat_quality_verdicts(verdicts)
    action_claims = sum(1 for case in cases if case.get("potential_action_claim") is True)
    category_totals = Counter(str(case.get("category", "unknown")) for case in cases)
    category_passes = Counter(
        str(case.get("category", "unknown"))
        for case in cases
        if isinstance(case.get("quality"), Mapping) and case["quality"].get("quality_pass") is True
    )
    return {
        "cases": len(cases),
        "quality": quality,
        "potential_action_claim_count": action_claims,
        "potential_action_claim_rate": (action_claims / len(cases)) if cases else None,
        "category_quality": {
            category: {
                "total": category_totals[category],
                "quality_pass": category_passes[category],
                "quality_pass_rate": category_passes[category] / category_totals[category],
            }
            for category in sorted(category_totals)
        },
    }


def quality_benchmark_summary(cases: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize the stricter behavioural benchmark without hiding failures."""

    total = len(cases)
    passed = sum(1 for case in cases if isinstance(case.get("quality"), Mapping) and case["quality"].get("quality_pass") is True)
    categories: Counter[str] = Counter()
    for case in cases:
        quality = case.get("quality")
        if isinstance(quality, Mapping):
            for category in quality.get("failure_categories", []):
                if isinstance(category, str):
                    categories[category] += 1
    action_claims = sum(1 for case in cases if case.get("potential_action_claim") is True)
    return {
        "cases": total,
        "quality_pass_count": passed,
        "quality_fail_count": total - passed,
        "quality_pass_rate": (passed / total) if total else None,
        "failure_category_counts": dict(sorted(categories.items())),
        "potential_action_claim_count": action_claims,
        "potential_action_claim_rate": (action_claims / total) if total else None,
    }


def _load_quality_benchmark_runner() -> Any:
    path = TRAINING_ROOT.parent / "benchmarks" / "run_performance_benchmark.py"
    spec = importlib.util.spec_from_file_location("peva_local_chat_quality_benchmark", path)
    if spec is None or spec.loader is None:
        raise EvaluationError(f"cannot load local quality benchmark runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model-path", type=Path, required=True, help="Reviewed local HF base below training/models/.")
    parser.add_argument("--adapter", type=Path, help="Optional immutable local LoRA adapter below outputs/adapters/.")
    parser.add_argument("--suite", choices=("chat_split", "quality_benchmark"), default="chat_split")
    parser.add_argument("--input", type=Path, help="Optional local JSONL input. Defaults to the chosen suite's canonical input.")
    parser.add_argument("--name", required=True, help="Safe file-name stem for this evaluation.")
    parser.add_argument("--max-new-tokens", type=int, default=96, help="Greedy reply cap; must be 8-256.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", args.name):
        raise EvaluationError("--name must use letters, digits, dot, underscore, and hyphen only")
    if not 8 <= args.max_new_tokens <= 256:
        raise EvaluationError("--max-new-tokens must be in [8, 256]")
    base = require_within(args.base_model_path, MODELS_ROOT, "--base-model-path")
    if not base.is_dir() or not (base / "config.json").is_file():
        raise EvaluationError("--base-model-path must be a local Hugging Face checkpoint")
    adapter = require_within(args.adapter, ADAPTERS_ROOT, "--adapter") if args.adapter else None
    if adapter and (not adapter.is_dir() or not (adapter / "adapter_config.json").is_file()):
        raise EvaluationError("--adapter must be an immutable PEFT adapter directory")
    default_input = DEFAULT_TEST if args.suite == "chat_split" else DEFAULT_QUALITY_BENCHMARK
    requested_input = args.input or default_input
    if args.suite == "quality_benchmark":
        # The social benchmark is frozen beside the training namespace rather
        # than inside it.  Permit exactly that one local fixture, never an
        # arbitrary path supplied via --input.
        input_path = requested_input.expanduser().resolve()
        if input_path != DEFAULT_QUALITY_BENCHMARK.resolve():
            raise EvaluationError("quality_benchmark uses only its frozen canonical fixture")
    else:
        input_path = safe_within_training(requested_input if requested_input.is_absolute() else TRAINING_ROOT / requested_input)
    if not input_path.is_file():
        raise EvaluationError(f"protected input is missing: {input_path}")

    report_path = REPORTS_ROOT / f"chat_checkpoint_evaluation_{args.name}.json"
    cases_path = EVALUATIONS_ROOT / f"chat_checkpoint_evaluation_{args.name}_cases.jsonl"
    if report_path.exists() or cases_path.exists():
        raise EvaluationError("refusing to overwrite an existing evaluation; choose a different --name")
    EVALUATIONS_ROOT.mkdir(parents=True, exist_ok=True)

    rows = read_jsonl(input_path)
    if not rows:
        raise EvaluationError("protected input is empty")
    report: dict[str, Any] = {
        "started_at": utc_now(),
        "status": "running",
        "mode": "local_only_greedy_holdout_screening",
        "suite": args.suite,
        "guarantees": [
            "local_base_checkpoint_only",
            "local_adapter_only" if adapter else "base_only",
            "no_network_model_download",
            "no_last_z_runtime_or_adb_import",
            "no_game_actions",
            "protected_chat_holdout_opened_only_for_post_training_evaluation",
        ],
        "base_model": {"path": str(base), "sha256": sha256_directory(base)},
        "adapter": ({"path": str(adapter), "sha256": sha256_directory(adapter)} if adapter else None),
        "protected_input": {"path": str(input_path), "sha256": sha256_file(input_path), "rows": len(rows)},
        "generation": {"do_sample": False, "max_new_tokens": args.max_new_tokens},
        "cases_path": str(cases_path),
    }
    started = time.perf_counter()
    try:
        import torch
        from peft import PeftModel
        from unsloth import FastLanguageModel

        if not torch.cuda.is_available():
            raise EvaluationError("CUDA is unavailable; CPU evaluation is refused")
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=str(base),
            max_seq_length=2048,
            dtype=None,
            load_in_4bit=True,
            device_map={"": 0},
            local_files_only=True,
        )
        if adapter:
            model = PeftModel.from_pretrained(model, str(adapter), local_files_only=True)
        FastLanguageModel.for_inference(model)
        quality_runner = _load_quality_benchmark_runner() if args.suite == "quality_benchmark" else None
        cases: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise EvaluationError("protected input contains a non-object row")
            if args.suite == "chat_split":
                prompt, context = _prompt_and_context(row)
                text = generate_reply(model, tokenizer, prompt, max_new_tokens=args.max_new_tokens)
                quality = validate_player_ready_output(text, {"input": context, "grounding": context}, apostrophe_policy="reject")
                cases.append(
                    {
                        "record_id": row.get("record_id"),
                        "category": row.get("category"),
                        "length_band": row.get("length_band"),
                        "candidate_text": text,
                        "quality": quality,
                        "potential_action_claim": bool(_ACTION_CLAIM.search(text)),
                    }
                )
            else:
                if row.get("chat_v1_quality_case") is not True:
                    raise EvaluationError("quality benchmark input contains a non-Chat-V1 case")
                context = row.get("canonical_context")
                if not isinstance(context, Mapping):
                    raise EvaluationError(f"quality benchmark case {row.get('id', '<unknown>')} lacks canonical_context")
                prompt = render_chat_messages(context)
                text = generate_reply(model, tokenizer, prompt, max_new_tokens=args.max_new_tokens)
                quality = quality_runner.score_chat_output(text, dict(row))
                cases.append(
                    {
                        "record_id": row.get("id"),
                        "category": row.get("social_expectations", {}).get("intent") if isinstance(row.get("social_expectations"), Mapping) else None,
                        "length_band": row.get("length_bucket"),
                        "candidate_text": text,
                        "quality": quality,
                        "potential_action_claim": bool(_ACTION_CLAIM.search(text)),
                    }
                )
        with cases_path.open("x", encoding="utf-8", newline="\n") as handle:
            for case in cases:
                handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
        report.update(
            status="passed",
            finished_at=utc_now(),
            elapsed_seconds=round(time.perf_counter() - started, 3),
            cuda={"name": torch.cuda.get_device_name(torch.cuda.current_device()), "torch_cuda": torch.version.cuda},
            results=evaluation_summary(cases) if args.suite == "chat_split" else quality_benchmark_summary(cases),
        )
    except Exception as exc:
        report.update(status="failed", finished_at=utc_now(), error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report.setdefault("finished_at", utc_now())
        write_json(report_path, report)
    print(f"[OK] Chat checkpoint evaluation report: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DatasetError, EvaluationError, ValueError) as exc:
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        raise SystemExit(2)
