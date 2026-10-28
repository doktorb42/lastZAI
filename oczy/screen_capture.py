"""Dekodowanie jednego obrazu przekazanego przez adapter tylko-odczytowy."""

from __future__ import annotations

import cv2
import numpy as np

from .android_capture import AdbPngRunner, capture_png_bytes


def capture_frame(*, runner: AdbPngRunner | None = None) -> np.ndarray:
    """Decode one captured PNG into an in-memory BGR frame."""
    payload = capture_png_bytes(runner=runner)
    frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        raise RuntimeError("Last Z screen was not a valid PNG")
    return frame
