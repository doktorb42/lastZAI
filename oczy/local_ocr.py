"""One-shot, on-emulator OCR transport for the visible Last Z screen.

This is deliberately an on-demand bridge.  ADB writes a single framebuffer
image into PEva's app-specific external folder; the Android companion reads it
with its bundled local OCR and deletes the source image before returning text.
No image is downloaded to the computer and no Android service remains active.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from dataclasses import dataclass

from .android_bridge import COMMAND_RECEIVER, LOCAL_URI, CopyBridge, CopyBridgeError


ACTION_OCR_ADB_SCREENSHOT = "com.peva.copybridge.OCR_ADB_SCREENSHOT"
OCR_SCREENSHOT_PATH = (
    "/sdcard/Android/data/com.peva.copybridge/files/peva_ocr_on_demand.png"
)


@dataclass(frozen=True, slots=True)
class ScreenOcrResult:
    """The private, single-use text result produced by the Android companion."""

    status: str
    text: str
    captured_at_ms: int
    # Optional dedicated conversation-header text.  Older Android companions
    # simply leave this blank; callers must never infer it from bubble text.
    header_text: str = ""
    # Optional geometry from the same single OCR pass. It is held in memory
    # only and lets the runtime distinguish left/right chat bubbles without
    # guessing from the words themselves.
    layout: "ScreenOcrLayout | None" = None
    # A separate bottom-of-screen OCR field. It is used only immediately
    # before an approved automatic reply so PEva cannot overwrite a human
    # draft. Old Helper versions leave this as uninspected, which blocks send.
    composer_text: str = ""
    composer_inspected: bool = False


@dataclass(frozen=True, slots=True)
class OcrLayoutLine:
    """One local OCR line with its position inside the cropped chat image."""

    text: str
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True, slots=True)
class ScreenOcrLayout:
    """Optional structured geometry returned by newer Copy Bridge versions."""

    width: int
    lines: tuple[OcrLayoutLine, ...]


class ScreenOcrBridge(CopyBridge):
    """Capture one current screen and receive its locally recognized text."""

    def capture_visible_screen(self) -> None:
        """Start one authenticated local OCR read without opening a game UI."""
        # Provider results are read-once.  Remove an abandoned earlier result
        # before creating a new image, so callers cannot mistake stale text for
        # the currently visible chat.
        self.take_result()
        token = self._read_token()
        try:
            self._checked(
                "shell", "mkdir", "-p",
                "/sdcard/Android/data/com.peva.copybridge/files",
            )
            self._checked("shell", "screencap", "-p", OCR_SCREENSHOT_PATH)
            self._checked(
                "shell", "am", "broadcast",
                "-n", COMMAND_RECEIVER,
                "-a", ACTION_OCR_ADB_SCREENSHOT,
                "--es", "token", token,
                "--es", "image_path", OCR_SCREENSHOT_PATH,
            )
        except CopyBridgeError:
            # Do not leave an unread chat image behind if delivery fails.
            try:
                self._checked("shell", "rm", "-f", OCR_SCREENSHOT_PATH)
            except CopyBridgeError:
                pass
            raise

    def take_result(self) -> ScreenOcrResult | None:
        """Return and erase the current OCR result, if one is available."""
        completed = self._checked("shell", "content", "query", "--uri", f"{LOCAL_URI}/ocr")
        raw = bytes(completed.stdout or b"").decode("utf-8", errors="strict")
        result = _parse_ocr_row(raw)
        return result if result.captured_at_ms > 0 else None

    def wait_for_result(self, *, timeout_seconds: float = 8.0) -> ScreenOcrResult:
        """Wait only for one explicitly-started read, never for a listener."""
        if not 1.0 <= float(timeout_seconds) <= 12.0:
            raise ValueError("timeout_seconds must be between 1 and 12")
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() < deadline:
            result = self.take_result()
            if result is not None:
                return result
            time.sleep(0.15)
        raise CopyBridgeError("PEva screen OCR did not return before its bounded timeout")


def _parse_ocr_row(raw: str) -> ScreenOcrResult:
    fields = {
        key.casefold(): value
        for key, value in re.findall(r"([A-Za-z0-9_]+)=([^,\s]*)", raw)
    }
    if not {"status_b64", "text_b64", "captured_at"}.issubset(fields):
        raise CopyBridgeError("PEva screen OCR response was invalid")
    try:
        status = _decode_base64(fields["status_b64"])
        text = _decode_base64(fields["text_b64"])
        header = _decode_base64(fields.get("header_b64", ""))
        layout = _decode_layout(fields.get("layout_b64", ""))
        composer_present = "composer_b64" in fields
        composer = _decode_base64(fields.get("composer_b64", ""))
        timestamp = int(fields["captured_at"])
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as error:
        raise CopyBridgeError("PEva screen OCR response was invalid") from error
    return ScreenOcrResult(status, text, timestamp, header, layout, composer, composer_present)


def _decode_base64(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def _decode_layout(value: str) -> ScreenOcrLayout | None:
    """Decode an optional, bounded OCR layout from a newer Android helper."""
    if not value:
        return None
    payload = json.loads(_decode_base64(value))
    if not isinstance(payload, dict):
        raise ValueError("layout must be an object")
    width = payload.get("width")
    raw_lines = payload.get("lines")
    if not isinstance(width, int) or not 1 <= width <= 10_000:
        raise ValueError("layout width is invalid")
    if not isinstance(raw_lines, list) or len(raw_lines) > 80:
        raise ValueError("layout lines are invalid")
    lines: list[OcrLayoutLine] = []
    for item in raw_lines:
        if not isinstance(item, dict):
            raise ValueError("layout line is invalid")
        text = item.get("text")
        coordinates = tuple(item.get(name) for name in ("left", "top", "right", "bottom"))
        if (
            not isinstance(text, str)
            or not text.strip()
            or any(not isinstance(coordinate, int) for coordinate in coordinates)
        ):
            raise ValueError("layout line is invalid")
        left, top, right, bottom = coordinates
        if not (0 <= left < right <= width and 0 <= top < bottom <= 20_000):
            raise ValueError("layout coordinates are invalid")
        lines.append(OcrLayoutLine(text.strip(), left, top, right, bottom))
    return ScreenOcrLayout(width, tuple(lines))
