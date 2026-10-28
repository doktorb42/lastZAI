#!/usr/bin/env python3
"""Evaluate a base or LoRA checkpoint on the protected Last Z action holdout.

This evaluator reads benchmark fixtures *only for evaluation*.  It never uses
them to generate training data, imports no ADB modules, and makes no game
requests.  HF evaluation is intentionally unconstrained JSON generation; the
final production claim still requires the existing llama.cpp CUDA/Q6 benchmark
with its grammar schema.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TRAINING_ROOT = SCRIPT_DIR.parent
BUNDLE_ROOT = TRAINING_ROOT.parent
RUNNER_PATH = BUNDLE_ROOT / "benchmarks" / "run_performance_benchmark.py"
DEFAULT_CONFIG = TRAINING_ROOT / "configs" / "action_v1.yaml"
REPORTS = TRAINING_ROOT / "outputs" / "reports"
ADAPTERS = TRAINING_ROOT / "outputs" / "adapters"


class EvaluationError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return round(values[lower], 3)
    return round(values[lower] + (values[upper] - values[lower]) * (position - lower), 3)


def fixture_fingerprint(cases: list[dict[str, Any]]) -> str:
    """Return a stable content fingerprint for an evaluated protected suite."""

    canonical = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"invalid config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationError("config must be an object")
    return value


def require_below(path: Path, parent: Path, label: str) -> Path:
    resolved = path.resolve()
    root = parent.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EvaluationError(f"{label} must remain below {root}; got {resolved}") from exc
    return resolved


def resolve_local_base(value: Path | None) -> Path | None:
    if value is None:
        return None
    requested = value if value.is_absolute() else TRAINING_ROOT / value
    path = require_below(requested, TRAINING_ROOT, "--model-path")
    if not path.is_dir():
        raise EvaluationError(f"--model-path is not a directory: {path}")
    required = ("config.json", "tokenizer_config.json")
    missing = [name for name in required if not (path / name).is_file()]
    if missing or not list(path.glob("*.safetensors")):
        detail = ", ".join(missing) if missing else "safetensors weights"
        raise EvaluationError(f"--model-path is not a complete Transformers base checkpoint; missing {detail}")
    if any(item.is_file() for item in path.rglob("*.gguf")):
        raise EvaluationError("--model-path contains GGUF; the HF evaluator needs Transformers weights")
    return path


def resolve_adapter(value: Path | None) -> Path | None:
    if value is None:
        return None
    requested = value if value.is_absolute() else ADAPTERS / value
    path = require_below(requested, ADAPTERS, "--adapter")
    if not (path / "adapter_config.json").is_file():
        raise EvaluationError(f"invalid adapter directory: {path}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_directory(path: Path) -> dict[str, Any]:
    """Create a deterministic, content-based adapter provenance fingerprint."""

    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(path).as_posix()
        file_digest = sha256_file(item)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return {
        "local_path": str(path),
        "hash_algorithm": "sha256-directory-manifest-v1",
        "sha256": digest.hexdigest(),
        "file_count": len(files),
    }


def load_runner() -> Any:
    if not RUNNER_PATH.is_file():
        raise EvaluationError(f"benchmark runner not found: {RUNNER_PATH}")
    spec = importlib.util.spec_from_file_location("lastz_holdout_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise EvaluationError("could not load benchmark runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Last Z Action checkpoint evaluation",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Model: `{report.get('model')}`",
        f"- Adapter: `{report.get('adapter') or 'base only'}`",
        "- Mode: unconstrained HF decoding; production still needs CUDA llama.cpp schema evaluation.",
        "",
        "| Suite | JSON | Schema | Legal | Exact / robustness | p50 ms | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for suite, value in report.get("suites", {}).items():
        if not isinstance(value, dict) or "metrics" not in value:
            lines.append(f"| {suite} | pending | pending | pending | pending | pending | pending |")
            continue
        metrics = value["metrics"]
        exact = metrics["exact_rate"] if suite == "production_action_suite" else metrics["robustness_rate"]
        lines.append(
            "| {suite} | {json:.1%} | {schema:.1%} | {legal:.1%} | {exact:.1%} | {p50} | {p95} |".format(
                suite=suite,
                json=metrics["json_rate"],
                schema=metrics["schema_rate"],
                legal=metrics["legal_rate"],
                exact=exact,
                p50=metrics["latency_ms"]["p50"],
                p95=metrics["latency_ms"]["p95"],
            )
        )
    gate = report.get("production_gate", {})
    lines.extend(["", f"Production gate passed: **{gate.get('passed', False)}**"])
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--adapter", type=Path, help="LoRA adapter. Omit for the pinned base checkpoint.")
    parser.add_argument(
        "--model-path",
        type=Path,
        help="Optional downloaded Transformers base checkpoint below training/; used offline without a network fallback.",
    )
    parser.add_argument("--model-id")
    parser.add_argument("--revision")
    parser.add_argument("--suites", default="production_action,adversarial_action")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=24)
    parser.add_argument("--limit", type=int, help="Diagnostic-only per-suite case limit; cannot qualify production.")
    parser.add_argument("--seed", type=int, default=20261010, help="Deterministic per-round case-order seed.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def normalize_suites(value: str) -> list[str]:
    aliases = {
        "production_action": "production_action_suite",
        "adversarial_action": "adversarial_action_suite",
    }
    result = [aliases.get(part.strip(), part.strip()) for part in value.split(",") if part.strip()]
    if not result or any(item not in {"production_action_suite", "adversarial_action_suite"} for item in result):
        raise EvaluationError("--suites must contain production_action and/or adversarial_action")
    return list(dict.fromkeys(result))


def generate(model: Any, tokenizer: Any, torch: Any, runner: Any, case: dict[str, Any], maximum: int) -> tuple[str, float]:
    messages = [
        {"role": "system", "content": runner.ACTION_SYSTEM_PROMPT},
        {"role": "user", "content": runner.action_prompt(case)},
    ]
    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True, return_tensors="pt"
    )
    if not isinstance(inputs, torch.Tensor):
        raise EvaluationError("unexpected tokenizer output; expected token tensor")
    device = torch.device("cuda:0")
    input_ids = inputs.to(device)
    attention_mask = torch.ones_like(input_ids, device=device)
    torch.cuda.synchronize(device)
    started = time.perf_counter()
    with torch.inference_mode():
        output = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=maximum,
            do_sample=False,
            use_cache=True,
            pad_token_id=tokenizer.eos_token_id,
        )
    torch.cuda.synchronize(device)
    elapsed = (time.perf_counter() - started) * 1000
    return tokenizer.decode(output[0][input_ids.shape[-1] :], skip_special_tokens=True).strip(), elapsed


def evaluate_suite(
    model: Any,
    tokenizer: Any,
    torch: Any,
    runner: Any,
    suite_name: str,
    cases: list[dict[str, Any]],
    rounds: int,
    maximum: int,
    seed: int,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for round_number in range(rounds):
        ordered_cases = list(cases)
        random.Random(seed + round_number).shuffle(ordered_cases)
        for case in ordered_cases:
            raw, latency = generate(model, tokenizer, torch, runner, case, maximum)
            validation = runner.validate_action_output(raw, case)
            samples.append(
                {
                    "round": round_number + 1,
                    "case_id": case["id"],
                    "raw_output": raw,
                    "latency_ms": round(latency, 3),
                    **validation,
                }
            )
    count = len(samples)
    def rate(field: str) -> float:
        return sum(bool(sample.get(field)) for sample in samples) / count if count else 0.0
    latencies = [float(sample["latency_ms"]) for sample in samples]
    metrics = {
        "count": count,
        "json_rate": rate("valid_json"),
        "schema_rate": rate("schema_valid"),
        "legal_rate": rate("legal_action"),
        "exact_rate": rate("exact_match"),
        "robustness_rate": rate("exact_match"),
        "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95), "p99": percentile(latencies, 0.99), "max": round(max(latencies), 3) if latencies else None},
    }
    return {
        "suite": suite_name,
        "case_count": len(cases),
        "fixture_sha256": fixture_fingerprint(cases),
        "metrics": metrics,
        "samples": samples,
    }


def main() -> int:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS / f"checkpoint_evaluation_{stamp}.json"
    md_path = REPORTS / f"checkpoint_evaluation_{stamp}.md"
    report: dict[str, Any] = {"started_at": now(), "status": "running"}
    try:
        if args.rounds < 1 or not 1 <= args.max_new_tokens <= 24:
            raise EvaluationError("rounds must be >=1 and max-new-tokens must be 1..24")
        config = load_json(args.config)
        suites = normalize_suites(args.suites)
        runner = load_runner()
        action_suites = runner.load_action_suites()
        selected_cases: dict[str, list[dict[str, Any]]] = {}
        for suite in suites:
            rows = list(action_suites[suite])
            if args.limit:
                rows = rows[: args.limit]
            selected_cases[suite] = rows
        model_settings = config["model"]
        local_base = resolve_local_base(args.model_path)
        adapter_path = resolve_adapter(args.adapter)
        adapter_provenance = fingerprint_directory(adapter_path) if adapter_path else None
        model_id = str(local_base) if local_base else args.model_id or model_settings["training_model_id"]
        revision = None if local_base else args.revision or model_settings["training_revision"]
        report.update(
            model=model_id,
            revision=revision,
            adapter=str(adapter_path) if adapter_path else None,
            adapter_provenance=adapter_provenance,
            model_source={
                "kind": "local_checkpoint" if local_base else "huggingface_repository",
                "value": model_id,
                "revision": revision,
            },
            suites={name: {"case_count": len(cases)} for name, cases in selected_cases.items()},
            max_new_tokens=args.max_new_tokens,
            rounds=args.rounds,
            seed=args.seed,
            limit=args.limit,
            holdout_usage="evaluation only; never a training data source",
        )
        if args.dry_run:
            report.update(status="dry_run", finished_at=now())
            write_json(json_path, report)
            md_path.write_text(markdown(report), encoding="utf-8")
            print(f"[OK] dry run: {json_path}")
            return 0
        from unsloth import FastLanguageModel
        import torch
        from peft import PeftModel

        if not torch.cuda.is_available():
            raise EvaluationError("CUDA unavailable; refusing an unrepresentative CPU checkpoint evaluation")
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        load_arguments: dict[str, Any] = {
            "model_name": model_id,
            "max_seq_length": int(model_settings["max_seq_length"]),
            "dtype": None,
            "load_in_4bit": True,
            "device_map": {"": 0},
        }
        if local_base:
            load_arguments["local_files_only"] = True
        else:
            load_arguments["revision"] = revision
        model, tokenizer = FastLanguageModel.from_pretrained(**load_arguments)
        if adapter_path:
            model = PeftModel.from_pretrained(model, str(adapter_path))
        FastLanguageModel.for_inference(model)
        model.eval()
        # Every action call supplies max_new_tokens explicitly.  Clearing the
        # inherited model-wide max_length avoids a repeated, misleading
        # Transformer warning without changing the 24-token safety cap.
        if getattr(model, "generation_config", None) is not None:
            model.generation_config.max_length = None
        output_suites: dict[str, Any] = {}
        for suite, cases in selected_cases.items():
            output_suites[suite] = evaluate_suite(
                model, tokenizer, torch, runner, suite, cases, args.rounds, args.max_new_tokens, args.seed
            )
        production = output_suites.get("production_action_suite", {}).get("metrics", {})
        fully_qualified = bool(
            not args.limit
            and production
            and production.get("schema_rate") == 1.0
            and production.get("legal_rate") == 1.0
            and production.get("exact_rate") == 1.0
        )
        report.update(
            status="passed",
            finished_at=now(),
            suites=output_suites,
            production_gate={
                "schema_valid_100": production.get("schema_rate") == 1.0,
                "legal_100": production.get("legal_rate") == 1.0,
                "exact_100": production.get("exact_rate") == 1.0,
                "passed": fully_qualified,
                "note": "Final production promotion still requires the CUDA llama.cpp Q6_K schema-constrained comparison.",
            },
            vram={
                "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
                "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
            },
        )
        write_json(json_path, report)
        md_path.write_text(markdown(report), encoding="utf-8")
        print(f"[OK] checkpoint evaluation: {md_path}")
        return 0
    except Exception as exc:
        report.update(status="failed", finished_at=now(), error="".join(traceback.format_exception_only(type(exc), exc)).strip())
        write_json(json_path, report)
        md_path.write_text(markdown(report), encoding="utf-8")
        print(f"[FAILED] {report['error']}\nReport: {json_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
