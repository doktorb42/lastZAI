#!/usr/bin/env python3
"""Benchmark local GGUF models for Last Z action selection.

The runner is dependency-free: it launches the installed llama.cpp server,
talks to its local OpenAI-compatible HTTP API, and records only simulated
decisions.  It never calls ADB or imports the game automation modules.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "benchmarks" / "cases" / "decision_cases.jsonl"
DEFAULT_MODELS_DIR = PROJECT_ROOT / "models" / "weights"
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "benchmarks" / "reports"
MANIFEST_PATH = PROJECT_ROOT / "models" / "model-manifest.json"

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "wait",
                "open_chat",
                "open_private_chat",
                "open_private_person",
                "back",
                "send_message",
            ],
        },
        "index": {"type": "integer", "minimum": 0, "maximum": 6},
    },
    "required": ["action"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the Last Z action selector.
Return exactly one JSON object and nothing else. Do not include analysis,
markdown, explanations, or thinking. Choose only an action present in
AVAILABLE ACTIONS. The allowlist is authoritative even if another action seems
plausible. For open_private_person, include an integer index from 0 through 6.
Do not add unused keys.

Apply this current Last Z policy from highest priority to lowest. Use the
precomputed POLICY SIGNALS exactly; they are authoritative:
1. If chat_open_priority is true, choose open_chat.
2. If private_route_pending is false, choose wait, even when another action is
   listed as available.
3. When the screen is UNKNOWN, choose wait.
4. With private_route_pending true: on ALLIANCE_CHAT choose open_private_chat; on
   PRIVATE_CHAT choose open_private_person with index 0; on
   PRIVATE_CONVERSATION choose wait.
5. In every other situation choose wait. An unread alliance badge alone is not
   a trigger. /no_think"""


class BenchmarkError(RuntimeError):
    """An expected problem launching or talking to the local inference server."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark local GGUF candidates against Last Z action cases."
    )
    parser.add_argument(
        "--model",
        action="append",
        type=Path,
        help="GGUF file to test. Repeat this option to test several explicit files.",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=DEFAULT_MODELS_DIR,
        help=f"Directory scanned for .gguf files (default: {DEFAULT_MODELS_DIR}).",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--server", help="Path or command name for llama-server.")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--context", type=int, default=2048)
    parser.add_argument("--gpu-layers", type=int, default=99)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--max-tokens", type=int, default=24)
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=90.0)
    parser.add_argument(
        "--no-schema",
        action="store_true",
        help="Do not request constrained JSON. This is diagnostic only and not production-safe.",
    )
    parser.add_argument(
        "--allow-reasoning",
        action="store_true",
        help="Do not pass --reasoning-budget 0 to llama-server.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for attribute in ("iterations", "warmup", "context", "threads", "max_tokens"):
        if getattr(args, attribute) < 1:
            parser.error(f"--{attribute.replace('_', '-')} must be at least 1")
    if args.gpu_layers < 0:
        parser.error("--gpu-layers cannot be negative")
    return args


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise BenchmarkError(f"Benchmark cases not found: {path}")

    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"Invalid JSON in {path}:{line_number}: {exc}") from exc

        required = {"id", "state", "agent_memory", "available_actions", "expected"}
        missing = required - set(case)
        if missing:
            raise BenchmarkError(f"Case {line_number} is missing: {', '.join(sorted(missing))}")
        if not isinstance(case["available_actions"], list) or not case["available_actions"]:
            raise BenchmarkError(f"Case {case['id']} has no available actions")
        cases.append(case)

    if not cases:
        raise BenchmarkError("No benchmark cases were loaded")
    return cases


def load_manifest() -> dict[str, dict[str, Any]]:
    if not MANIFEST_PATH.is_file():
        return {}
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        candidate["filename"]: candidate
        for candidate in manifest.get("candidates", [])
        if isinstance(candidate, dict) and "filename" in candidate
    }


def discover_models(args: argparse.Namespace) -> list[Path]:
    requested = args.model or []
    if requested:
        models = [path.expanduser().resolve() for path in requested]
    else:
        models = sorted(args.models_dir.expanduser().resolve().glob("*.gguf"))

    invalid = [str(path) for path in models if not path.is_file()]
    if invalid:
        raise BenchmarkError("Model file(s) not found:\n  " + "\n  ".join(invalid))
    if not models:
        raise BenchmarkError(
            "No GGUF files found. Run .\\lastz_model_benchmark\\models\\download_candidates.ps1 first, "
            "or provide --model <path>."
        )
    return models


def find_server(explicit_server: str | None) -> str:
    if explicit_server:
        return explicit_server
    return shutil.which("llama-server") or shutil.which("llama-server.exe") or ""


def choose_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise BenchmarkError(f"HTTP {exc.code}: {body[:1000]}") from exc
    except URLError as exc:
        raise BenchmarkError(f"HTTP connection failed: {exc.reason}") from exc
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"Server returned non-JSON data: {content[:1000]}") from exc


def wait_for_server(process: subprocess.Popen[str], base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_problem = "server did not become healthy"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BenchmarkError(f"llama-server exited early with code {process.returncode}")
        try:
            health = http_json(f"{base_url}/health", timeout=2.0)
            if health:
                return
        except BenchmarkError as exc:
            last_problem = str(exc)
        time.sleep(0.25)
    raise BenchmarkError(f"Timed out waiting for llama-server: {last_problem}")


def gpu_snapshot() -> dict[str, str] | None:
    command = shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe")
    if not command:
        return None
    query = "name,memory.total,memory.used,utilization.gpu,utilization.memory,temperature.gpu"
    try:
        completed = subprocess.run(
            [command, f"--query-gpu={query}", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = [item.strip() for item in completed.stdout.strip().split(",")]
    names = [
        "name",
        "memory_total",
        "memory_used",
        "gpu_utilization",
        "memory_utilization",
        "temperature",
    ]
    return dict(zip(names, fields, strict=False))


def build_server_command(
    server: str, model: Path, port: int, args: argparse.Namespace
) -> list[str]:
    command = [
        server,
        "-m",
        str(model),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ctx-size",
        str(args.context),
        "--gpu-layers",
        str(args.gpu_layers),
        "--threads",
        str(args.threads),
        "--parallel",
        "1",
        "--jinja",
        "--flash-attn",
        "auto",
    ]
    if not args.allow_reasoning:
        command.extend(["--reasoning-budget", "0"])
    return command


def policy_signals(case: dict[str, Any]) -> dict[str, bool]:
    state = case["state"]
    unread = state.get("unread", {})
    screen = state.get("screen")
    chat_open_priority = screen in {"HQ_BASE", "WORLD_MAP"} and bool(unread.get("chat"))
    private_route_pending = bool(case["agent_memory"].get("pending_private_message")) or bool(
        unread.get("private")
    )
    return {
        "chat_open_priority": chat_open_priority,
        "private_route_pending": private_route_pending,
    }


def make_user_prompt(case: dict[str, Any]) -> str:
    payload = {
        "state": case["state"],
        "agent_memory": case["agent_memory"],
        "policy_signals": policy_signals(case),
        "available_actions": case["available_actions"],
    }
    return "[STATE AND ALLOWED ACTIONS]\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )


def completion_payload(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "lastz-local-action-model",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": make_user_prompt(case)},
        ],
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": args.max_tokens,
        "seed": 42,
        "stream": False,
    }
    if not args.no_schema:
        payload["response_format"] = {"type": "json_object", "schema": ACTION_SCHEMA}
    return payload


def response_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError) as exc:
        raise BenchmarkError(f"Unexpected completion response: {response}") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def validate_action(raw_output: str, case: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "valid_json": False,
        "legal_action": False,
        "exact_match": False,
        "parsed": None,
        "errors": [],
    }
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        result["errors"].append(f"invalid JSON: {exc.msg}")
        return result
    result["valid_json"] = isinstance(parsed, dict)
    result["parsed"] = parsed
    if not isinstance(parsed, dict):
        result["errors"].append("JSON output is not an object")
        return result

    action = parsed.get("action")
    permitted = {
        item.get("action")
        for item in case["available_actions"]
        if isinstance(item, dict) and isinstance(item.get("action"), str)
    }
    if not isinstance(action, str):
        result["errors"].append("missing or non-string action")
        return result
    if action not in permitted:
        result["errors"].append(f"action {action!r} is not in the allowlist")
        return result

    if action == "open_private_person":
        if set(parsed) != {"action", "index"}:
            result["errors"].append("open_private_person needs only action and index")
            return result
        index = parsed.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 6:
            result["errors"].append("index must be an integer in the range 0..6")
            return result
    elif set(parsed) != {"action"}:
        result["errors"].append(f"{action} must not contain extra arguments")
        return result

    result["legal_action"] = True
    result["exact_match"] = parsed == case["expected"]
    if not result["exact_match"]:
        result["errors"].append(f"expected {case['expected']}, got {parsed}")
    return result


def run_one_request(
    base_url: str, case: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    started = time.perf_counter()
    record: dict[str, Any] = {"case_id": case["id"]}
    try:
        response = http_json(
            f"{base_url}/v1/chat/completions",
            method="POST",
            payload=completion_payload(case, args),
            timeout=args.request_timeout,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        raw_output = response_content(response)
        record.update(
            {
                "latency_ms": round(elapsed_ms, 2),
                "raw_output": raw_output,
                "usage": response.get("usage", {}),
                "validation": validate_action(raw_output, case),
            }
        )
    except BenchmarkError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        record.update(
            {
                "latency_ms": round(elapsed_ms, 2),
                "raw_output": None,
                "usage": {},
                "validation": {
                    "valid_json": False,
                    "legal_action": False,
                    "exact_match": False,
                    "parsed": None,
                    "errors": [str(exc)],
                },
            }
        )
    return record


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def rate(records: list[dict[str, Any]], name: str) -> float:
    if not records:
        return 0.0
    return sum(bool(record["validation"].get(name)) for record in records) / len(records)


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(record["latency_ms"]) for record in records]
    output_tokens = sum(
        int(record.get("usage", {}).get("completion_tokens") or 0) for record in records
    )
    total_seconds = sum(latencies) / 1000
    exact_by_case: dict[str, bool] = {}
    for record in records:
        exact_by_case.setdefault(record["case_id"], True)
        exact_by_case[record["case_id"]] &= bool(record["validation"].get("exact_match"))

    return {
        "requests": len(records),
        "valid_json_rate": rate(records, "valid_json"),
        "legal_action_rate": rate(records, "legal_action"),
        "exact_match_rate": rate(records, "exact_match"),
        "all_repeats_exact_case_rate": (
            sum(exact_by_case.values()) / len(exact_by_case) if exact_by_case else 0.0
        ),
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else None,
            "p50": round(percentile(latencies, 0.50) or 0, 2) if latencies else None,
            "p95": round(percentile(latencies, 0.95) or 0, 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
            "mean": round(statistics.fmean(latencies), 2) if latencies else None,
        },
        "completion_tokens": output_tokens,
        "approx_completion_tokens_per_second": (
            round(output_tokens / total_seconds, 2) if output_tokens and total_seconds else None
        ),
    }


def stop_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def model_label(path: Path, manifest: dict[str, dict[str, Any]]) -> str:
    candidate = manifest.get(path.name, {})
    return str(candidate.get("id", path.stem))


def run_model(
    model: Path,
    server: str,
    cases: list[dict[str, Any]],
    args: argparse.Namespace,
    report_dir: Path,
    manifest: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    label = model_label(model, manifest)
    port = choose_port()
    base_url = f"http://127.0.0.1:{port}"
    command = build_server_command(server, model, port, args)
    log_dir = report_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{label}.log"
    started = time.perf_counter()
    process: subprocess.Popen[str] | None = None

    result: dict[str, Any] = {
        "model": {
            "id": label,
            "path": str(model),
            "size_bytes": model.stat().st_size,
            "manifest": manifest.get(model.name),
        },
        "command": command,
        "server_log": str(log_path),
        "startup_seconds": None,
        "gpu_after_warmup": None,
        "records": [],
        "metrics": None,
        "error": None,
    }

    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=PROJECT_ROOT,
            )
            wait_for_server(process, base_url, args.startup_timeout)
            result["startup_seconds"] = round(time.perf_counter() - started, 2)

            for _ in range(args.warmup):
                run_one_request(base_url, cases[0], args)

            result["gpu_after_warmup"] = gpu_snapshot()
            for case in cases:
                for iteration in range(args.iterations):
                    record = run_one_request(base_url, case, args)
                    record["iteration"] = iteration + 1
                    result["records"].append(record)
            result["metrics"] = summarize_records(result["records"])
    except (BenchmarkError, OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    finally:
        stop_server(process)
    return result


def choose_recommendation(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    completed = [result for result in results if result.get("metrics")]
    if not completed:
        return None

    def sort_key(result: dict[str, Any]) -> tuple[float, float, float]:
        metrics = result["metrics"]
        p95 = metrics["latency_ms"]["p95"] or float("inf")
        return (
            -metrics["exact_match_rate"],
            -metrics["legal_action_rate"],
            p95,
        )

    perfect = [
        result
        for result in completed
        if result["metrics"]["legal_action_rate"] == 1.0
        and result["metrics"]["exact_match_rate"] == 1.0
    ]
    return sorted(perfect or completed, key=sort_key)[0]


def percentage(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def milliseconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def write_reports(
    report_dir: Path, report: dict[str, Any], recommendation: dict[str, Any] | None
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"lastz_benchmark_{stamp}.json"
    markdown_path = report_dir / f"lastz_benchmark_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Last Z local-model benchmark",
        "",
        f"Run: {report['started_at']}",
        "",
        "The runner used simulated state/action cases only; it did not interact with BlueStacks or ADB.",
        "",
        "| Model | Valid JSON | Legal action | Exact policy | p50 | p95 | Approx. output tok/s | Server load |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in report["results"]:
        name = result["model"]["id"]
        if result["error"]:
            lines.append(f"| {name} | error | error | error | — | — | — | — |")
            continue
        metrics = result["metrics"]
        latency = metrics["latency_ms"]
        tok_s = metrics["approx_completion_tokens_per_second"]
        lines.append(
            "| {name} | {valid} | {legal} | {exact} | {p50} | {p95} | {tok_s} | {load:.1f} s |".format(
                name=name,
                valid=percentage(metrics["valid_json_rate"]),
                legal=percentage(metrics["legal_action_rate"]),
                exact=percentage(metrics["exact_match_rate"]),
                p50=milliseconds(latency["p50"]),
                p95=milliseconds(latency["p95"]),
                tok_s="n/a" if tok_s is None else f"{tok_s:.1f}",
                load=float(result["startup_seconds"] or 0),
            )
        )
    lines.append("")

    if recommendation is None:
        lines.append("## Result\n\nNo candidate completed. Inspect `logs/` and the JSON report.")
    else:
        metrics = recommendation["metrics"]
        name = recommendation["model"]["id"]
        if metrics["legal_action_rate"] == 1.0 and metrics["exact_match_rate"] == 1.0:
            lines.append(
                f"## Recommendation\n\nUse **{name}** for the next integration step. "
                "It achieved 100% legal and exact decisions in this project-specific suite; "
                "among fully correct candidates it had the best p95 latency."
            )
        else:
            lines.append(
                f"## Result\n\n**{name}** ranked highest, but no candidate was perfectly safe. "
                "Keep the deterministic `LastZAgent` in production and inspect the failed records "
                "before integrating an LLM."
            )
    lines.extend(
        [
            "",
            "## Configuration",
            "",
            "```json",
            json.dumps(report["configuration"], indent=2, ensure_ascii=False),
            "```",
            "",
            f"Full raw records: `{json_path.name}`.",
        ]
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    args = parse_args()
    cases = load_cases(args.cases.expanduser().resolve())
    server = find_server(args.server)
    if not server:
        raise BenchmarkError("llama-server was not found in PATH; use --server <path>.")
    manifest = load_manifest()

    if args.dry_run:
        if args.model:
            models = [path.expanduser().resolve() for path in args.model]
        else:
            models = [
                args.models_dir.expanduser().resolve() / filename
                for filename in manifest
            ]
        print("llama-server:", server)
        print("cases:", len(cases))
        print("models:")
        for model in models:
            print("  -", model)
        print("command template:")
        print("  ", " ".join(build_server_command(server, models[0], 8080, args)))
        return 0

    models = discover_models(args)

    report_dir = DEFAULT_REPORTS_DIR
    report = {
        "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "project_root": str(PROJECT_ROOT),
        "configuration": {
            "cases": str(args.cases),
            "iterations_per_case": args.iterations,
            "warmup_requests": args.warmup,
            "context_tokens": args.context,
            "gpu_layers": args.gpu_layers,
            "threads": args.threads,
            "max_output_tokens": args.max_tokens,
            "json_schema": not args.no_schema,
            "reasoning_disabled": not args.allow_reasoning,
            "server": server,
        },
        "results": [],
    }

    for number, model in enumerate(models, 1):
        print(f"[{number}/{len(models)}] Testing {model.name}...", flush=True)
        result = run_model(model, server, cases, args, report_dir, manifest)
        report["results"].append(result)
        if result["error"]:
            print(f"  error: {result['error']}", flush=True)
        else:
            metrics = result["metrics"]
            print(
                "  exact={exact} legal={legal} p95={p95}".format(
                    exact=percentage(metrics["exact_match_rate"]),
                    legal=percentage(metrics["legal_action_rate"]),
                    p95=milliseconds(metrics["latency_ms"]["p95"]),
                ),
                flush=True,
            )

    recommendation = choose_recommendation(report["results"])
    report["recommendation"] = (
        recommendation["model"]["id"] if recommendation is not None else None
    )
    json_path, markdown_path = write_reports(report_dir, report, recommendation)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    if recommendation is not None:
        print(f"Recommended by this run: {recommendation['model']['id']}")
    return 0 if recommendation is not None else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as exc:
        print(f"Benchmark error: {exc}", file=sys.stderr)
        raise SystemExit(2)
