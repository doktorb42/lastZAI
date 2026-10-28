#!/usr/bin/env python3
"""Create a local-only readiness report for Last Z fine-tuning.

The script deliberately does not invoke ADB, BlueStacks, game executors, model
downloads, or training code.  It only inspects the host and writes reports below
``training/outputs/reports``. Its optional WSL probe runs only ``uname``,
``python3 --version`` and ``nvidia-smi`` inside an existing distribution.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCRIPT_VERSION = "1.1.0"
TRAINING_ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = TRAINING_ROOT / "outputs" / "reports"
PACKAGE_NAMES = (
    "torch",
    "transformers",
    "datasets",
    "accelerate",
    "peft",
    "trl",
    "bitsandbytes",
    "unsloth",
    "huggingface_hub",
    "safetensors",
)
SAFE_STEM = re.compile(r"[^A-Za-z0-9_.-]+")
WSL_EMBEDDED_ERROR_MARKERS = (
    "access is denied",
    "odmowa dost",
    "cannot create swap",
    "nie mo",  # Covers Polish text even if the console replaces its diacritics.
    "wsl_e_",
    "wsl/service/",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_stem(value: str) -> str:
    cleaned = SAFE_STEM.sub("-", value.strip()).strip(".-")
    if not cleaned:
        raise ValueError("Output stem must contain at least one letter or digit.")
    return cleaned[:100]


def decode_command_output(value: bytes | str | None) -> str:
    """Decode regular UTF-8 output and Windows' UTF-16LE WSL output safely."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if b"\x00" in value:
        try:
            return value.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace")


def truncate(value: str | None, limit: int = 4000) -> str:
    if value is None:
        return ""
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n… [truncated]"


def command_result(command: Iterable[str], timeout_seconds: int = 15) -> dict[str, Any]:
    """Run a local diagnostic command without a shell and return serializable data."""

    command_list = [str(part) for part in command]
    try:
        completed = subprocess.run(
            command_list,
            capture_output=True,
            text=False,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return {
            "command": command_list,
            "available": False,
            "returncode": None,
            "stdout": "",
            "stderr": "Executable was not found.",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command_list,
            "available": True,
            "returncode": None,
            "stdout": truncate(decode_command_output(exc.stdout)),
            "stderr": "Timed out after {} seconds.".format(timeout_seconds),
        }
    except OSError as exc:
        return {
            "command": command_list,
            "available": False,
            "returncode": None,
            "stdout": "",
            "stderr": "{}: {}".format(type(exc).__name__, exc),
        }

    return {
        "command": command_list,
        "available": True,
        "returncode": completed.returncode,
        "stdout": truncate(decode_command_output(completed.stdout)),
        "stderr": truncate(decode_command_output(completed.stderr)),
    }


def find_nvidia_smi() -> str | None:
    executable = shutil.which("nvidia-smi")
    if executable:
        return executable

    if os.name == "nt":
        candidates = (
            Path(os.environ.get("ProgramW6432", r"C:\\Program Files"))
            / "NVIDIA Corporation"
            / "NVSMI"
            / "nvidia-smi.exe",
            Path(os.environ.get("ProgramFiles", r"C:\\Program Files"))
            / "NVIDIA Corporation"
            / "NVSMI"
            / "nvidia-smi.exe",
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def parse_mib(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value or "")
    return float(match.group(0)) if match else None


def inspect_nvidia_smi() -> dict[str, Any]:
    executable = find_nvidia_smi()
    if not executable:
        return {
            "available": False,
            "executable": None,
            "gpus": [],
            "query": None,
            "note": "nvidia-smi was not found on PATH or in the standard Windows NVSMI directory.",
        }

    fields = [
        "index",
        "name",
        "driver_version",
        "memory.total",
        "memory.free",
        "memory.used",
        "compute_cap",
    ]
    query = command_result(
        [
            executable,
            "--query-gpu=" + ",".join(fields),
            "--format=csv,noheader,nounits",
        ]
    )
    active_query = query
    active_fields = fields
    # Older driver tools do not expose compute_cap.  Keep the core information
    # rather than treating a newer optional field as a total diagnostic failure.
    if query["returncode"] != 0:
        active_fields = fields[:-1]
        active_query = command_result(
            [
                executable,
                "--query-gpu=" + ",".join(active_fields),
                "--format=csv,noheader,nounits",
            ]
        )

    gpus: list[dict[str, Any]] = []
    if active_query["returncode"] == 0:
        for row in csv.reader(active_query["stdout"].splitlines()):
            if len(row) != len(active_fields):
                continue
            item = {field: cell.strip() for field, cell in zip(active_fields, row)}
            for field in ("memory.total", "memory.free", "memory.used"):
                item[field + "_mib"] = parse_mib(item.pop(field, ""))
            item["compute_capability"] = item.pop("compute_cap", None)
            gpus.append(item)

    return {
        "available": active_query["available"] and active_query["returncode"] == 0,
        "executable": executable,
        "gpus": gpus,
        "query": active_query,
        "initial_query": query if active_query is not query else None,
        "version": command_result([executable, "--version"]),
    }


def inspect_memory() -> dict[str, Any]:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        state = MemoryStatus()
        state.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            return {
                "source": "GlobalMemoryStatusEx",
                "total_bytes": int(state.ullTotalPhys),
                "available_bytes": int(state.ullAvailPhys),
                "total_gib": round(state.ullTotalPhys / 1024**3, 2),
                "available_gib": round(state.ullAvailPhys / 1024**3, 2),
            }

    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        fields: dict[str, int] = {}
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, raw_value = line.partition(":")
            number = re.search(r"\d+", raw_value)
            if number:
                fields[key] = int(number.group(0)) * 1024
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable")
        if total is not None:
            return {
                "source": "/proc/meminfo",
                "total_bytes": total,
                "available_bytes": available,
                "total_gib": round(total / 1024**3, 2),
                "available_gib": round(available / 1024**3, 2) if available is not None else None,
            }

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        total = int(page_size) * int(page_count)
        return {
            "source": "os.sysconf",
            "total_bytes": total,
            "available_bytes": None,
            "total_gib": round(total / 1024**3, 2),
            "available_gib": None,
        }
    except (AttributeError, OSError, ValueError):
        return {"source": "unavailable", "total_bytes": None, "available_bytes": None}


def inspect_disks() -> list[dict[str, Any]]:
    paths = [TRAINING_ROOT, Path.cwd(), Path(os.environ.get("HF_HOME", TRAINING_ROOT / "hf-cache"))]
    seen: set[str] = set()
    reports: list[dict[str, Any]] = []
    for raw_path in paths:
        try:
            path = raw_path.expanduser().resolve()
        except OSError:
            path = raw_path.expanduser()
        # disk_usage requires an existing path.  For an absent cache directory,
        # check its closest existing parent instead.
        probe = path
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        key = str(probe)
        if key in seen:
            continue
        seen.add(key)
        try:
            usage = shutil.disk_usage(probe)
        except OSError as exc:
            reports.append({"path": str(probe), "error": "{}: {}".format(type(exc).__name__, exc)})
            continue
        reports.append(
            {
                "path": str(probe),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "total_gib": round(usage.total / 1024**3, 2),
                "free_gib": round(usage.free / 1024**3, 2),
            }
        )
    return reports


def inspect_packages() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for package in PACKAGE_NAMES:
        try:
            result[package] = {"installed": True, "version": importlib.metadata.version(package)}
        except importlib.metadata.PackageNotFoundError:
            result[package] = {"installed": False, "version": None}
    return result


def inspect_torch(skip_probe: bool) -> dict[str, Any]:
    package = {"installed": False, "version": None}
    try:
        package = {"installed": True, "version": importlib.metadata.version("torch")}
    except importlib.metadata.PackageNotFoundError:
        return {**package, "probed": False, "reason": "The torch package is not installed."}

    if skip_probe:
        return {**package, "probed": False, "reason": "Skipped by --skip-torch-probe."}

    try:
        import torch  # type: ignore[import-not-found]

        cuda_available = bool(torch.cuda.is_available())
        item: dict[str, Any] = {
            **package,
            "probed": True,
            "cuda_available": cuda_available,
            "cuda_runtime": getattr(torch.version, "cuda", None),
            "hip_runtime": getattr(torch.version, "hip", None),
            "device_count": int(torch.cuda.device_count()) if cuda_available else 0,
        }
        devices: list[dict[str, Any]] = []
        if cuda_available:
            for index in range(item["device_count"]):
                props = torch.cuda.get_device_properties(index)
                free_bytes, total_bytes = torch.cuda.mem_get_info(index)
                devices.append(
                    {
                        "index": index,
                        "name": props.name,
                        "compute_capability": "{}.{}".format(props.major, props.minor),
                        "total_memory_bytes": int(props.total_memory),
                        "total_memory_gib": round(props.total_memory / 1024**3, 2),
                        "free_memory_bytes": int(free_bytes),
                        "free_memory_gib": round(free_bytes / 1024**3, 2),
                    }
                )
        item["devices"] = devices
        return item
    except Exception as exc:  # The diagnostic must remain useful if CUDA init fails.
        return {
            **package,
            "probed": True,
            "error": "{}: {}".format(type(exc).__name__, exc),
        }


def inspect_platform() -> dict[str, Any]:
    release = platform.release()
    version = platform.version()
    wsl_markers = "microsoft" in release.lower() or "microsoft" in version.lower() or bool(os.environ.get("WSL_DISTRO_NAME"))
    item: dict[str, Any] = {
        "os_name": os.name,
        "sys_platform": sys.platform,
        "system": platform.system(),
        "release": release,
        "version": version,
        "machine": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "is_wsl": wsl_markers,
        "wsl_distro_name": os.environ.get("WSL_DISTRO_NAME"),
    }
    if os.name == "nt":
        item["wsl_status"] = command_result(["wsl.exe", "--status"])
        item["wsl_distributions"] = command_result(["wsl.exe", "--list", "--verbose"])
    return item


def wsl_command(distro: str | None, command: list[str]) -> list[str]:
    """Build a shell-free, read-only command for a pre-existing WSL distro."""

    result = ["wsl.exe"]
    if distro:
        result.extend(["--distribution", distro])
    return [*result, "--", *command]


def has_embedded_wsl_error(result: dict[str, Any]) -> bool:
    """Detect launcher diagnostics that WSL can emit despite a zero exit code."""

    output = "\n".join((result.get("stdout") or "", result.get("stderr") or "")).casefold()
    return any(marker in output for marker in WSL_EMBEDDED_ERROR_MARKERS)


def probe_wsl(distro: str | None) -> dict[str, Any]:
    """Run only harmless WSL diagnostics and preserve every failure in the report."""

    if os.name != "nt":
        return {
            "requested": True,
            "status": "not_applicable",
            "distro": distro,
            "note": "The host is not native Windows. Run this inspector inside the active Linux/WSL environment instead.",
            "commands": {},
            "errors": [],
        }
    if distro is not None and (not distro.strip() or "\x00" in distro or distro.strip().startswith("-")):
        return {
            "requested": True,
            "status": "failed",
            "distro": distro,
            "note": "The supplied WSL distribution name is invalid.",
            "commands": {},
            "errors": ["--wsl-distro must be a non-empty name without NUL characters and must not start with '-'."],
        }
    if not shutil.which("wsl.exe"):
        return {
            "requested": True,
            "status": "failed",
            "distro": distro,
            "note": "wsl.exe was not found on PATH.",
            "commands": {},
            "errors": ["Cannot run the requested WSL diagnostics because wsl.exe is unavailable."],
        }

    checks = {
        "uname": ["uname", "-a"],
        "python": ["python3", "--version"],
        "nvidia_smi": ["nvidia-smi"],
    }
    results = {
        name: command_result(wsl_command(distro.strip() if distro else None, command), timeout_seconds=25)
        for name, command in checks.items()
    }
    errors: list[str] = []
    for name, result in results.items():
        if not result.get("available") or result.get("returncode") != 0:
            detail = result.get("stderr") or result.get("stdout") or "No diagnostic output was returned."
            errors.append("{} failed: {}".format(name, detail))
            continue
        # A WSL launch issue can be written to stderr even when the requested
        # command exits successfully. Treat it as a readiness blocker instead
        # of hiding a swap-VHD/access-denied problem behind a zero exit code.
        diagnostic_stderr = (result.get("stderr") or "").strip()
        if diagnostic_stderr or has_embedded_wsl_error(result):
            detail = diagnostic_stderr or (result.get("stdout") or "").strip()
            errors.append("{} returned a WSL diagnostic despite exit code 0: {}".format(name, detail))
    return {
        "requested": True,
        "status": "passed" if not errors else "failed",
        "distro": distro.strip() if distro else None,
        "target": distro.strip() if distro else "WSL default distribution",
        "note": "Only uname, python3 --version and nvidia-smi were executed through wsl.exe; no installation or state change was requested.",
        "commands": results,
        "errors": errors,
    }


def inspect_cuda_toolkit() -> dict[str, Any]:
    nvcc = shutil.which("nvcc")
    return {
        "cuda_home": os.environ.get("CUDA_HOME") or os.environ.get("CUDA_PATH"),
        "nvcc": command_result([nvcc, "--version"]) if nvcc else {"available": False, "stderr": "nvcc was not found on PATH."},
    }


def environment_variables() -> dict[str, Any]:
    # Deliberately omit token-bearing variables such as HF_TOKEN.
    interesting = ("VIRTUAL_ENV", "CONDA_PREFIX", "CUDA_HOME", "CUDA_PATH", "HF_HOME", "TRANSFORMERS_CACHE", "WSL_DISTRO_NAME")
    return {name: os.environ.get(name) for name in interesting if os.environ.get(name)}


def build_findings(report: dict[str, Any], min_free_vram_gib: float, min_free_disk_gib: float) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    blockers: list[str] = []
    python_version = tuple(sys.version_info[:2])
    if python_version < (3, 10):
        blockers.append("Python {}.{} is older than 3.10.".format(*python_version))

    nvidia = report["nvidia_smi"]
    torch = report["torch"]
    if not nvidia["available"]:
        warnings.append("nvidia-smi did not return a usable GPU inventory.")
    if torch.get("installed") and torch.get("probed") and not torch.get("cuda_available"):
        warnings.append("Installed torch did not report CUDA availability.")
    if not torch.get("installed"):
        warnings.append("PyTorch is not installed in this interpreter.")
    if report["platform"]["system"] == "Windows":
        warnings.append("Native Windows was detected. Confirm the intended WSL/Linux training environment before installing training dependencies.")
    wsl_probe = report.get("wsl_probe")
    if wsl_probe and wsl_probe.get("requested") and wsl_probe.get("status") == "failed":
        error_summary = "; ".join(wsl_probe.get("errors", [])) or "Unknown WSL diagnostic failure."
        blockers.append("Requested WSL diagnostic probe failed: {}".format(error_summary))

    free_vram = [gpu.get("memory.free_mib") for gpu in nvidia.get("gpus", []) if gpu.get("memory.free_mib") is not None]
    if min_free_vram_gib > 0:
        required_mib = min_free_vram_gib * 1024
        if not free_vram:
            blockers.append("Cannot verify the requested minimum free VRAM ({:.2f} GiB).".format(min_free_vram_gib))
        elif max(free_vram) < required_mib:
            blockers.append(
                "Maximum observed free VRAM is {:.2f} GiB, below the requested {:.2f} GiB."
                .format(max(free_vram) / 1024, min_free_vram_gib)
            )

    free_disk = [entry.get("free_gib") for entry in report["disk"] if entry.get("free_gib") is not None]
    if min_free_disk_gib > 0 and (not free_disk or max(free_disk) < min_free_disk_gib):
        blockers.append("No inspected filesystem meets the requested free disk threshold of {:.2f} GiB.".format(min_free_disk_gib))

    return warnings, blockers


def markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def bytes_to_gib(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return "{:.2f} GiB".format(value / 1024**3)


def render_markdown(report: dict[str, Any]) -> str:
    platform_info = report["platform"]
    lines = [
        "# Last Z fine-tuning environment report",
        "",
        "- Generated (UTC): `{}`".format(report["generated_at_utc"]),
        "- Script version: `{}`".format(report["script_version"]),
        "- Scope: local system diagnostics only; no game, ADB, BlueStacks, model download, or training operation was performed.",
        "",
        "## Host",
        "",
        "| Field | Value |",
        "| --- | --- |",
        "| System | {} |".format(markdown_escape(platform_info["system"])),
        "| Release | {} |".format(markdown_escape(platform_info["release"])),
        "| Architecture | {} |".format(markdown_escape(platform_info["machine"])),
        "| WSL detected | {} |".format(platform_info["is_wsl"]),
        "| Python | {} |".format(markdown_escape(report["python"]["version"])),
        "| Python executable | `{}` |".format(markdown_escape(report["python"]["executable"])),
        "",
        "## GPU and CUDA",
        "",
    ]
    gpus = report["nvidia_smi"].get("gpus", [])
    if gpus:
        lines.extend(["| GPU | Driver | Compute capability | Total VRAM | Free VRAM |", "| --- | --- | --- | ---: | ---: |"])
        for gpu in gpus:
            lines.append(
                "| {} | {} | {} | {} MiB | {} MiB |".format(
                    markdown_escape(gpu.get("name", "unknown")),
                    markdown_escape(gpu.get("driver_version", "unknown")),
                    markdown_escape(gpu.get("compute_capability") or "not reported"),
                    gpu.get("memory.total_mib", "n/a"),
                    gpu.get("memory.free_mib", "n/a"),
                )
            )
    else:
        lines.append("No usable `nvidia-smi` GPU inventory was returned.")
    wsl_probe = report.get("wsl_probe")
    lines.extend(
        [
            "",
            "- `nvidia-smi`: {}".format("available" if report["nvidia_smi"]["available"] else "unavailable"),
            "- `nvcc`: {}".format("available" if report["cuda_toolkit"]["nvcc"].get("available") else "unavailable"),
            "- Torch CUDA available: {}".format(report["torch"].get("cuda_available", "not probed")),
            "",
            "## Memory and disk",
            "",
            "- System RAM: total {}, available {}.".format(
                bytes_to_gib(report["memory"].get("total_bytes")), bytes_to_gib(report["memory"].get("available_bytes"))
            ),
            "",
            "| Checked path | Total | Free |",
            "| --- | ---: | ---: |",
        ]
    )
    for disk in report["disk"]:
        if "error" in disk:
            lines.append("| `{}` | n/a | {} |".format(markdown_escape(disk["path"]), markdown_escape(disk["error"])))
        else:
            lines.append("| `{}` | {} GiB | {} GiB |".format(markdown_escape(disk["path"]), disk["total_gib"], disk["free_gib"]))

    if wsl_probe and wsl_probe.get("requested"):
        lines.extend(
            [
                "",
                "## Optional WSL probe",
                "",
                "- Target: `{}`".format(markdown_escape(wsl_probe.get("target") or wsl_probe.get("distro") or "not applicable")),
                "- Status: `{}`".format(markdown_escape(wsl_probe.get("status", "unknown"))),
                "- Scope: only `uname -a`, `python3 --version`, and `nvidia-smi` were requested through `wsl.exe`.",
            ]
        )
        commands = wsl_probe.get("commands", {})
        if commands:
            lines.extend(["", "| Diagnostic | Return code | Result | Output / error |", "| --- | ---: | --- | --- |"])
            for name, command in commands.items():
                returncode = command.get("returncode")
                success = command.get("available") and returncode == 0
                detail = command.get("stderr") or command.get("stdout") or "-"
                if len(detail) > 600:
                    detail = detail[:600] + "... [truncated in Markdown; see JSON]"
                lines.append(
                    "| `{}` | {} | {} | {} |".format(
                        name,
                        returncode if returncode is not None else "n/a",
                        "passed" if success else "failed",
                        markdown_escape(detail),
                    )
                )
        if wsl_probe.get("errors"):
            lines.extend(["", "WSL probe errors (these are blockers when the probe was requested):", ""])
            lines.extend("- {}".format(markdown_escape(error)) for error in wsl_probe["errors"])

    lines.extend(["", "## Python packages", "", "| Package | Installed | Version |", "| --- | --- | --- |"])
    for name, package in report["packages"].items():
        lines.append("| `{}` | {} | {} |".format(name, package["installed"], package["version"] or "-"))

    lines.extend(["", "## Findings", ""])
    if report["blockers"]:
        lines.append("### Blockers")
        lines.append("")
        lines.extend("- {}".format(item) for item in report["blockers"])
        lines.append("")
    if report["warnings"]:
        lines.append("### Warnings")
        lines.append("")
        lines.extend("- {}".format(item) for item in report["warnings"])
        lines.append("")
    if not report["blockers"] and not report["warnings"]:
        lines.append("No requested threshold failure was found. This is a diagnostic result, not a training approval.")
        lines.append("")

    reproduce = "python training/scripts/inspect_environment.py --min-free-vram-gib {} --min-free-disk-gib {}".format(
        report["requested_thresholds"]["min_free_vram_gib"], report["requested_thresholds"]["min_free_disk_gib"]
    )
    if wsl_probe and wsl_probe.get("requested"):
        reproduce += " --probe-wsl"
        if wsl_probe.get("distro"):
            reproduce += ' --wsl-distro "{}"'.format(wsl_probe["distro"].replace('"', "\\\""))
    lines.extend(
        [
            "## Reproduce",
            "",
            "```powershell",
            reproduce,
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect the local fine-tuning environment without downloading models or interacting with Last Z."
    )
    parser.add_argument("--output-stem", default=None, help="Optional safe filename stem, written only below training/outputs/reports.")
    parser.add_argument(
        "--skip-torch-probe",
        action="store_true",
        help="Do not import torch or initialize its CUDA check; package metadata is still inspected.",
    )
    parser.add_argument(
        "--min-free-vram-gib",
        type=float,
        default=0.0,
        help="Optional requested VRAM threshold. A missed threshold is reported as a blocker.",
    )
    parser.add_argument(
        "--min-free-disk-gib",
        type=float,
        default=0.0,
        help="Optional requested disk threshold. A missed threshold is reported as a blocker.",
    )
    parser.add_argument(
        "--probe-wsl",
        action="store_true",
        help="On native Windows, run only uname, python3 --version and nvidia-smi in the selected existing WSL distribution.",
    )
    parser.add_argument(
        "--wsl-distro",
        help="Optional existing WSL distribution name for --probe-wsl (default: the WSL default distribution).",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 if the report contains blockers.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_free_vram_gib < 0 or args.min_free_disk_gib < 0:
        print("ERROR: requested thresholds cannot be negative.", file=sys.stderr)
        return 2
    if args.wsl_distro and not args.probe_wsl:
        print("ERROR: --wsl-distro requires --probe-wsl.", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "schema_version": 1,
        "script": "inspect_environment.py",
        "script_version": SCRIPT_VERSION,
        "generated_at_utc": utc_now(),
        "scope": {
            "network_access": False,
            "model_download": False,
            "training": False,
            "adb_or_bluestacks": False,
            "wsl_probe_requested": bool(args.probe_wsl),
        },
        "training_root": str(TRAINING_ROOT),
        "python": {
            "version": sys.version.replace("\n", " "),
            "version_info": list(sys.version_info[:3]),
            "executable": sys.executable,
            "pip": command_result([sys.executable, "-m", "pip", "--version"]),
        },
        "platform": inspect_platform(),
        "environment": environment_variables(),
        "memory": inspect_memory(),
        "disk": inspect_disks(),
        "packages": inspect_packages(),
        "nvidia_smi": inspect_nvidia_smi(),
        "cuda_toolkit": inspect_cuda_toolkit(),
        "torch": inspect_torch(args.skip_torch_probe),
        "wsl_probe": probe_wsl(args.wsl_distro) if args.probe_wsl else {"requested": False, "status": "not_requested"},
        "requested_thresholds": {
            "min_free_vram_gib": args.min_free_vram_gib,
            "min_free_disk_gib": args.min_free_disk_gib,
        },
    }
    report["warnings"], report["blockers"] = build_findings(
        report, args.min_free_vram_gib, args.min_free_disk_gib
    )
    stem = safe_stem(args.output_stem) if args.output_stem else "environment_" + timestamp()
    json_path = REPORTS_ROOT / (stem + ".json")
    markdown_path = REPORTS_ROOT / (stem + ".md")
    if json_path.exists() or markdown_path.exists():
        print("ERROR: report already exists: {}".format(stem), file=sys.stderr)
        return 2

    write_report(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    write_report(markdown_path, render_markdown(report))
    print("Wrote JSON report: {}".format(json_path))
    print("Wrote Markdown report: {}".format(markdown_path))
    if report["blockers"]:
        print("Completed with {} blocker(s).".format(len(report["blockers"])), file=sys.stderr)
        return 2 if args.strict else 0
    print("Completed with {} warning(s).".format(len(report["warnings"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
