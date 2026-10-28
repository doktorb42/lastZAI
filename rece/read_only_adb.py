"""Minimalne gesty ADB dla Rąk odczytu, bez tekstu i wysyłania.

Ten adapter ma celowo małą powierzchnię: pojedynczy tap oraz ograniczony swipe.
Nie korzysta z historycznego transportu i nie posiada funkcji klawiatury,
pola czatu, przycisku Send ani komend Last Z.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from config.runtime_config import ADB_DEVICE, ADB_PATH


GestureRunner = Callable[..., Any]


def _subprocess_runner(*command: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(list(command), **kwargs)


def tap(x: int, y: int, *, runner: GestureRunner | None = None) -> bool:
    """Perform one validated Android tap; no other operation is exposed."""
    if int(x) <= 0 or int(y) <= 0:
        return False
    run = _subprocess_runner if runner is None else runner
    try:
        result = run(
            ADB_PATH, "-s", ADB_DEVICE, "shell", "input", "tap", str(int(x)), str(int(y)),
            capture_output=True, text=False, timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(getattr(result, "returncode", 1) == 0)


def swipe(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int,
    *,
    runner: GestureRunner | None = None,
) -> bool:
    """Perform one validated swipe; callers restrict its geometry further."""
    if min(int(x1), int(y1), int(x2), int(y2), int(duration_ms)) <= 0:
        return False
    run = _subprocess_runner if runner is None else runner
    try:
        result = run(
            ADB_PATH,
            "-s",
            ADB_DEVICE,
            "shell",
            "input",
            "swipe",
            str(int(x1)),
            str(int(y1)),
            str(int(x2)),
            str(int(y2)),
            str(int(duration_ms)),
            capture_output=True,
            text=False,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(getattr(result, "returncode", 1) == 0)
