"""Offline review runner for PEva Agency adapters on held-out real chat context.

This runner deliberately has no ADB, Last Z, HTTP, or send-path imports.  It
loads the pinned Qwen base plus a supplied PEFT adapter and produces proposals
for human review only.  The real candidate source remains review-only and is
verified byte-for-byte before and after every run.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from trening.agency_benchmark import _load_runner, _messages, is_benchmark_action_safe, preflight
from trening.agency_types import AgencyDecision


ROOT = Path(__file__).resolve().parents[1]
REAL_CANDIDATES = ROOT / "data" / "training" / "agency" / "real" / "peva_real_candidates_v0.jsonl"
V2_ADAPTER = ROOT / "data" / "training" / "runs" / "peva-qwen2.5-7b-agency-pilot-v2" / "adapter"
RESULTS_DIR = ROOT / "data" / "training" / "agency" / "real_situation_tests"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_real_candidates(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    source_bytes = path.read_bytes()
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(source_bytes.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"real candidate row {line_number} must be an object")
        if row.get("synthetic") is not False or row.get("eligible_for_positive_training") is not False:
            raise ValueError(f"real candidate row {line_number} lost its review-only boundary")
        if not isinstance(row.get("incoming_text"), str) or not row["incoming_text"].strip():
            raise ValueError(f"real candidate row {line_number} lacks incoming_text")
        if not isinstance(row.get("context", []), list):
            raise ValueError(f"real candidate row {line_number} context must be a list")
        rows.append(row)
    if not rows:
        raise ValueError("real candidate source is empty")
    return rows, source_bytes


def build_case(row: Mapping[str, Any]) -> dict[str, Any]:
    """Build the same reactive shape used by the frozen Agency benchmark."""
    context = [str(item).strip() for item in row.get("context", []) if str(item).strip()]
    return {
        "id": str(row["id"]),
        "mode": "AGENCY_REACTIVE",
        "context": context,
        "memories": [],
        "input": str(row["incoming_text"]).strip(),
    }


def interpret_generation(case: Mapping[str, Any], raw: str) -> dict[str, Any]:
    """Apply the public Agency contract; malformed output becomes silent WAIT."""
    result: dict[str, Any] = {
        "case_id": str(case["id"]),
        "raw_output": str(raw),
        "schema_valid": False,
        "fallback_used": False,
    }
    try:
        decision = AgencyDecision.parse_reactive(raw)
        result["schema_valid"] = True
    except (TypeError, ValueError) as exc:
        decision = AgencyDecision.safe_fallback("AGENCY_REACTIVE")
        result["fallback_used"] = True
        result["parse_error"] = str(exc)

    result["decision"] = decision.to_dict()
    result["action_safe"] = is_benchmark_action_safe(decision.message, case.get("context", []))
    return result


def summarize(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(results)
    counts = Counter(str(item["decision"]["decision"]) for item in results)
    valid = sum(bool(item.get("schema_valid")) for item in results)
    safe = sum(bool(item.get("action_safe")) for item in results)
    return {
        "records": total,
        "schema_valid": valid,
        "schema_valid_rate": round(valid / total, 6) if total else 0.0,
        "action_safe": safe,
        "unsafe_action_claims": total - safe,
        "fallbacks": sum(bool(item.get("fallback_used")) for item in results),
        "decision_counts": dict(sorted(counts.items())),
    }


def output_path(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RESULTS_DIR / f"peva_v2_real_situation_review_{stamp}.json"


def atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a self-contained progress snapshot without a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def generate_case(tokenizer: Any, model: Any, case: Mapping[str, Any], *, max_new_tokens: int, torch_module: Any) -> str:
    """Generate one bounded offline proposal with the benchmark prompt shape."""
    prompt = tokenizer.apply_chat_template(_messages(case), tokenize=False, add_generation_prompt=True)
    encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
    device = model.get_input_embeddings().weight.device
    encoded = {key: value.to(device) for key, value in encoded.items()}
    prompt_length = encoded["input_ids"].shape[1]
    with torch_module.inference_mode():
        output = model.generate(
            **encoded,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    return tokenizer.decode(output[0, prompt_length:], skip_special_tokens=True).strip()


def progress_report(base: Mapping[str, Any], *, status: str, results: Sequence[Mapping[str, Any]], partial_path: Path, error: str | None = None) -> dict[str, Any]:
    report = dict(base)
    report.update({"status": status, "partial_report": str(partial_path), "summary": summarize(results), "results": list(results)})
    if error:
        report["error"] = error
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, default=V2_ADAPTER, help="PEFT adapter directory; defaults to Pilot V2")
    parser.add_argument("--source", type=Path, default=REAL_CANDIDATES, help="Review-only real candidates JSONL")
    parser.add_argument("--output", help="New JSON result file; existing files are never overwritten")
    parser.add_argument("--max-cases", type=int, default=0, help="Optional positive limit for a quick review")
    parser.add_argument("--max-new-tokens", type=int, default=32, help="Bound each proposal to 8-64 new tokens; defaults to 32")
    parser.add_argument("--time-limit-seconds", type=int, default=600, help="Stop safely between cases after this many seconds; defaults to 600")
    parser.add_argument("--dry-run", action="store_true", help="Validate source and print the plan without loading a model")
    args = parser.parse_args()

    if args.max_cases < 0:
        parser.error("--max-cases must be zero or positive")
    if not 8 <= args.max_new_tokens <= 64:
        parser.error("--max-new-tokens must be between 8 and 64")
    if args.time_limit_seconds < 60:
        parser.error("--time-limit-seconds must be at least 60")
    source = args.source.expanduser().resolve()
    adapter = args.adapter.expanduser().resolve()
    if not adapter.is_dir():
        parser.error(f"adapter directory does not exist: {adapter}")
    rows, original_bytes = load_real_candidates(source)
    if args.max_cases:
        rows = rows[: args.max_cases]
    destination = output_path(args.output)
    partial_destination = destination.with_name(destination.stem + ".partial.json")
    if destination.exists() or partial_destination.exists():
        parser.error(f"refusing to overwrite an existing review artifact: {destination}")

    base_report: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": utc_now(),
        "run_mode": "OFFLINE_HUMAN_REVIEW_ONLY",
        "guarantees": {
            "adb_used": False,
            "last_z_send_path_used": False,
            "network_used": False,
            "real_candidates_used_for_training": False,
        },
        "source": {"path": str(source), "sha256": sha256_bytes(original_bytes), "records_selected": len(rows)},
        "adapter": str(adapter),
        "generation": {"max_new_tokens": args.max_new_tokens, "time_limit_seconds": args.time_limit_seconds},
        "preflight": preflight(),
    }
    if args.dry_run:
        base_report["status"] = "ready" if base_report["preflight"].get("ready") else "blocked"
        print(json.dumps(base_report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if base_report["status"] == "ready" else 2

    if not base_report["preflight"].get("ready"):
        raise RuntimeError(f"inference preflight failed: {base_report['preflight'].get('reason', 'unknown reason')}")

    import torch

    results: list[dict[str, Any]] = []
    model = None
    started = time.monotonic()
    try:
        tokenizer, model, _dtype = _load_runner(str(adapter))
        for position, row in enumerate(rows, start=1):
            if time.monotonic() - started >= args.time_limit_seconds:
                report = progress_report(base_report, status="time_limit_reached", results=results, partial_path=partial_destination)
                atomic_json_write(partial_destination, report)
                print(json.dumps({"status": report["status"], "partial_report": str(partial_destination), "summary": report["summary"]}, ensure_ascii=False, sort_keys=True), flush=True)
                return 124
            case = build_case(row)
            raw = generate_case(tokenizer, model, case, max_new_tokens=args.max_new_tokens, torch_module=torch)
            result = interpret_generation(case, raw)
            result["position"] = position
            result["source_id"] = str(row["id"])
            result["input"] = case["input"]
            result["context"] = case["context"]
            results.append(result)
            report = progress_report(base_report, status="running", results=results, partial_path=partial_destination)
            atomic_json_write(partial_destination, report)
            print(json.dumps({"status": "running", "completed_cases": position, "total_cases": len(rows)}, ensure_ascii=False, sort_keys=True), flush=True)

        if source.read_bytes() != original_bytes:
            raise RuntimeError("real candidate source changed during this run; refusing to write a review report")
        report = progress_report(base_report, status="completed", results=results, partial_path=partial_destination)
        atomic_json_write(destination, report)
        atomic_json_write(partial_destination, report)
        print(json.dumps({"status": "completed", "output": str(destination), "summary": report["summary"]}, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    except BaseException as exc:
        report = progress_report(base_report, status="failed", results=results, partial_path=partial_destination, error=f"{type(exc).__name__}: {exc}")
        atomic_json_write(partial_destination, report)
        raise
    finally:
        if model is not None:
            del model
        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    raise SystemExit(main())
