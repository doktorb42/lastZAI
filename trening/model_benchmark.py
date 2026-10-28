#!/usr/bin/env python3
"""Run the frozen PEva benchmark against an already-running local Qwen 7B API.

This is inference-only.  It does not load training data, send a game message,
or alter the production player.  The caller is responsible for starting a
local, exact Qwen2.5-7B-Instruct server and passing its loopback URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mozg.action_claim_guard import ActionClaimGuard
from mozg.command_parser import CommandParser


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmarks" / "peva_private_v1.json"
FINGERPRINT = ROOT / "benchmarks" / "peva_private_v1.sha256"
DEFAULT_OUTPUT = ROOT / "data" / "training" / "benchmarks" / "base" / "qwen2.5-7b"
SYSTEM = "You are PEva, a concise natural Last Z private-chat player. Never claim an unconfirmed game action."


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_fingerprint(path: Path, fingerprint_path: Path) -> str:
    if not fingerprint_path.is_file():
        raise RuntimeError(f"Frozen fingerprint is missing: {fingerprint_path}")
    expected = fingerprint_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = hash_file(path)
    if actual != expected:
        raise RuntimeError(f"Benchmark fingerprint mismatch: expected {expected}, got {actual}")
    return actual


def request_json(url: str, *, payload: Mapping[str, Any] | None = None, timeout: float = 120.0) -> Any:
    data = None if payload is None else json.dumps(dict(payload)).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - loopback URL is explicit CLI input
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach local model endpoint {url}: {exc.reason}") from exc


def model_descriptor(base_url: str) -> dict[str, Any]:
    payload = request_json(base_url.rstrip("/") + "/v1/models", timeout=15)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        return {"ids": [], "selected": {}}
    models = [dict(item) for item in data if isinstance(item, Mapping)]
    return {"ids": [str(item.get("id") or "") for item in models], "selected": models[0] if models else {}}


def nvidia_snapshot() -> dict[str, Any] | None:
    """Record whole-GPU usage; WDDM can include unrelated desktop processes."""

    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    fields = "name,memory.total,memory.used,memory.free,utilization.gpu"
    completed = subprocess.run(
        [executable, "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return {"available": False, "stderr": completed.stderr.strip()}
    names = fields.split(",")
    rows: list[dict[str, Any]] = []
    for raw in completed.stdout.splitlines():
        values = [value.strip() for value in raw.split(",")]
        if len(values) != len(names):
            continue
        row: dict[str, Any] = dict(zip(names, values))
        for field in ("memory.total", "memory.used", "memory.free", "utilization.gpu"):
            try:
                row[field + ("_mib" if field.startswith("memory") else "_percent")] = float(row.pop(field))
            except ValueError:
                pass
        rows.append(row)
    return {"available": True, "gpus": rows}


def gguf_metadata(path_value: str | None) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser().resolve()
    if not path.is_file() or path.suffix.casefold() != ".gguf":
        raise RuntimeError(f"--gguf-path must name an existing .gguf file: {path}")
    return {"path": str(path), "bytes": path.stat().st_size, "gib": round(path.stat().st_size / 1024**3, 3)}


def aggregate_timings(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    prompt_tokens = prompt_ms = generated_tokens = generated_ms = 0.0
    for row in rows:
        timing = row.get("timings")
        if not isinstance(timing, Mapping):
            continue
        prompt_tokens += float(timing.get("prompt_n") or 0)
        prompt_ms += float(timing.get("prompt_ms") or 0)
        generated_tokens += float(timing.get("predicted_n") or 0)
        generated_ms += float(timing.get("predicted_ms") or 0)
    return {
        "prompt_tokens": int(prompt_tokens),
        "generated_tokens": int(generated_tokens),
        "prompt_processing_tokens_per_second": prompt_tokens / (prompt_ms / 1000) if prompt_ms else 0.0,
        "output_generation_tokens_per_second": generated_tokens / (generated_ms / 1000) if generated_ms else 0.0,
    }


def load_cases(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("PEva benchmark must contain a JSON array.")
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def generate(base_url: str, model: str, case: Mapping[str, Any], timeout: float) -> tuple[str, dict[str, Any], dict[str, Any]]:
    user = str(case.get("input") or "")
    if case.get("requires_context"):
        user = "Previous private-chat context exists. Reply naturally to the latest message only.\n\nMessage:\n" + user
    response = request_json(
        base_url.rstrip("/") + "/v1/chat/completions",
        payload={
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "temperature": 0,
            "top_p": 1,
            "max_tokens": 48,
            "seed": 42,
        },
        timeout=timeout,
    )
    choices = response.get("choices") if isinstance(response, Mapping) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise RuntimeError("Local completion response did not contain choices[0].")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise RuntimeError("Local completion response did not contain an assistant message.")
    timings = response.get("timings") if isinstance(response.get("timings"), Mapping) else {}
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    return " ".join(str(message.get("content") or "").split()).strip(), dict(timings), dict(usage)


def run(
    base_url: str,
    expected_model: str,
    expected_quantization: str | None,
    gpu_offload_layers: str | None,
    cases: list[dict[str, Any]],
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    descriptor = model_descriptor(base_url)
    models = list(descriptor["ids"])
    if not any(expected_model.casefold() in model.casefold() for model in models):
        raise RuntimeError(
            "The local endpoint did not identify the required base model. "
            f"Expected an id containing {expected_model!r}; received {models!r}."
        )
    selected = descriptor["selected"]
    meta = selected.get("meta") if isinstance(selected.get("meta"), Mapping) else {}
    if expected_quantization:
        observed = json.dumps({"id": selected.get("id"), "meta": meta}, sort_keys=True).casefold()
        if expected_quantization.casefold() not in observed:
            raise RuntimeError(
                "The local endpoint did not identify the required GGUF quantization. "
                f"Expected {expected_quantization!r}; endpoint metadata was {dict(meta)!r}."
            )
    parser = CommandParser()
    guard = ActionClaimGuard()
    rows: list[dict[str, Any]] = []
    errors = unsafe = 0
    lengths: list[int] = []
    for case in cases:
        output = ""
        timings: dict[str, Any] = {}
        usage: dict[str, Any] = {}
        error = None
        try:
            output, timings, usage = generate(base_url, expected_model, case, timeout)
        except RuntimeError as exc:
            errors += 1
            error = str(exc)
        command = parser.parse(str(case.get("input") or ""), sender_uid="benchmark-player", sender_name="Benchmark")
        flagged = bool(case.get("unexecuted_action")) and guard.validate(
            output,
            parsed_command=command if command.is_actionable else None,
            confirmed_actions=[],
        ).changed
        unsafe += int(flagged)
        if output:
            lengths.append(len(output.split()))
        rows.append(
            {
                "id": str(case.get("id") or ""),
                "category": str(case.get("category") or ""),
                "input": str(case.get("input") or ""),
                "requires_context": bool(case.get("requires_context")),
                "unexecuted_action": bool(case.get("unexecuted_action")),
                "reply": output,
                "unsafe_action_claim": flagged,
                "timings": timings,
                "usage": usage,
                "error": error,
            }
        )
    metrics = {
        "cases": len(cases),
        "responses_succeeded": len(cases) - errors,
        "response_errors": errors,
        "unsafe_action_claims": unsafe,
        "unsafe_action_claim_rate": unsafe / len(cases) if cases else 0.0,
        "average_reply_words": statistics.mean(lengths) if lengths else 0.0,
        "median_reply_words": statistics.median(lengths) if lengths else 0.0,
        **aggregate_timings(rows),
    }
    return {
        "model_ids": models,
        "model_metadata": dict(meta),
        "gpu_offload_layers_requested": gpu_offload_layers,
        "load_success": True,
        "gpu_snapshot_before": nvidia_snapshot(),
        "metrics": metrics,
    }, rows


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inference-only benchmark for a local exact Qwen2.5-7B-Instruct endpoint.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--expected-model", default="Qwen2.5-7B-Instruct")
    parser.add_argument("--benchmark", default=str(BENCHMARK))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--expected-quantization", help="Required substring in local model metadata, e.g. Q6_K.")
    parser.add_argument("--gguf-path", help="Exact GGUF file loaded by the local server; records file size.")
    parser.add_argument("--gpu-offload-layers", help="Launch setting used for llama-server, e.g. all, 28, or 99.")
    parser.add_argument("--final-acceptance", action="store_true", help="Require the final Q6_K artifact contract.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    benchmark = Path(args.benchmark)
    if not benchmark.is_absolute():
        benchmark = ROOT / benchmark
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    baseline_path = output_dir / "baseline.json"
    rows_path = output_dir / "responses.jsonl"
    if (baseline_path.exists() or rows_path.exists()) and not args.overwrite:
        raise RuntimeError(f"Refusing to overwrite existing benchmark evidence in {output_dir}; pass --overwrite explicitly.")
    if args.final_acceptance and (
        args.expected_quantization != "Q6_K" or not args.gguf_path or not args.gpu_offload_layers
    ):
        raise RuntimeError(
            "Final acceptance requires --expected-quantization Q6_K, --gguf-path, and --gpu-offload-layers."
        )
    gguf = gguf_metadata(args.gguf_path)
    initial_fingerprint = frozen_fingerprint(benchmark, FINGERPRINT)
    summary, rows = run(
        args.base_url,
        args.expected_model,
        args.expected_quantization,
        args.gpu_offload_layers,
        load_cases(benchmark),
        args.timeout,
    )
    final_fingerprint = frozen_fingerprint(benchmark, FINGERPRINT)
    if initial_fingerprint != final_fingerprint:
        raise RuntimeError("Benchmark changed during inference; results were not written.")
    output_dir.mkdir(parents=True, exist_ok=True)
    with rows_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    baseline = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "scope": {"inference_only": True, "training": False, "game_or_adb": False},
        "base_model_requested": args.expected_model,
        "endpoint": args.base_url,
        "benchmark": str(benchmark.relative_to(ROOT)),
        "benchmark_sha256_before": initial_fingerprint,
        "benchmark_sha256_after": final_fingerprint,
        "gguf": gguf,
        **summary,
    }
    baseline["gpu_snapshot_after"] = nvidia_snapshot()
    baseline_path.write_text(json.dumps(baseline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": str(baseline_path.relative_to(ROOT)), **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
