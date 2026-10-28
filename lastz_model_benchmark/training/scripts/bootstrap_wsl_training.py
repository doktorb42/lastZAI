#!/usr/bin/env python3
"""Run the isolated WSL training-environment bootstrap without PowerShell policy dependencies."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distro", default="Ubuntu")
    parser.add_argument("--execute", action="store_true", help="Allow the WSL script to create the venv and install packages.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script = Path(__file__).with_suffix(".sh").resolve()
    if not script.is_file():
        print(f"[BLOCKED] missing WSL bootstrap script: {script}", file=sys.stderr)
        return 2
    # WSL's Windows command-line shim can consume backslashes before wslpath
    # sees them.  A drive-qualified forward-slash form is unambiguous.
    windows_path = script.as_posix()
    translation = subprocess.run(
        ["wsl.exe", "--distribution", args.distro, "--", "wslpath", "-a", windows_path],
        text=True,
        capture_output=True,
        check=False,
    )
    if translation.returncode or not translation.stdout.strip():
        print(f"[BLOCKED] could not translate script path into WSL: {translation.stderr.strip()}", file=sys.stderr)
        return 2
    command = ["wsl.exe", "--distribution", args.distro, "--", "bash", translation.stdout.strip()]
    if args.execute:
        command.append("--execute")
    result = subprocess.run(command, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
