"""Jedyny adapter ADB używany przez Oczy do odczytu obrazu.

Moduł zna wyłącznie jeden rozkaz: pobranie PNG przez ``exec-out screencap``.
Nie importuje starego transportu, nie zapisuje obrazu na dysku, nie dotyka
ekranu i nie posiada żadnego API tekstu lub wysyłania wiadomości.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from config.runtime_config import ADB_DEVICE, ADB_PATH


AdbPngRunner = Callable[..., Any]


def _subprocess_runner(*command: str, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(list(command), **kwargs)


def capture_png_bytes(
    *,
    runner: AdbPngRunner | None = None,
    timeout: float = 15.0,
) -> bytes:
    """Return one Android screenshot PNG in memory, or fail without a partial image."""
    run = _subprocess_runner if runner is None else runner
    result = run(
        ADB_PATH,
        "-s",
        ADB_DEVICE,
        "exec-out",
        "screencap",
        "-p",
        capture_output=True,
        text=False,
        timeout=float(timeout),
    )
    payload = getattr(result, "stdout", None)
    if getattr(result, "returncode", 1) != 0 or not isinstance(payload, (bytes, bytearray)):
        raise RuntimeError("could not capture the Last Z screen")
    return bytes(payload)
