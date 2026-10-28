from __future__ import annotations

import base64
import subprocess

from oczy.local_ocr import (
    ACTION_OCR_ADB_SCREENSHOT,
    OCR_SCREENSHOT_PATH,
    ScreenOcrBridge,
    _parse_ocr_row,
)


def _response(command, stdout: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(command, 0, stdout, b"")


def test_parse_ocr_row_decodes_local_text() -> None:
    status = base64.urlsafe_b64encode("Odczyt lokalny zakończony".encode()).rstrip(b"=")
    text = base64.urlsafe_b64encode("hello\nthanks".encode()).rstrip(b"=")

    result = _parse_ocr_row(
        f"Row: 0 status_b64={status.decode()}, text_b64={text.decode()}, captured_at=42"
    )

    assert result.status == "Odczyt lokalny zakończony"
    assert result.text == "hello\nthanks"
    assert result.captured_at_ms == 42


def test_parse_ocr_row_decodes_optional_dedicated_header() -> None:
    status = base64.urlsafe_b64encode("ok".encode()).rstrip(b"=")
    text = base64.urlsafe_b64encode("hello".encode()).rstrip(b"=")
    header = base64.urlsafe_b64encode("LV.27 [ROXY]Cute Face".encode()).rstrip(b"=")

    result = _parse_ocr_row(
        f"Row: 0 status_b64={status.decode()}, text_b64={text.decode()}, "
        f"header_b64={header.decode()}, captured_at=43"
    )

    assert result.header_text == "LV.27 [ROXY]Cute Face"
    assert result.captured_at_ms == 43


def test_capture_uses_emulator_screenshot_and_authenticated_receiver() -> None:
    commands: list[tuple[str, ...]] = []
    responses = iter([
        b"Row: 0 status_b64=QnJhayDFm3dpZcW8ZWdvIG9kY3p5dHU, text_b64=, captured_at=0",
        b"Row: 0 token=" + b"a" * 32,
        b"",
        b"",
        b"Broadcast completed: result=0",
    ])

    def runner(command, timeout):
        commands.append(tuple(command))
        return _response(command, next(responses))

    bridge = ScreenOcrBridge(runner=runner)
    bridge.capture_visible_screen()

    flattened = [item for command in commands for item in command]
    assert "screencap" in flattened
    assert OCR_SCREENSHOT_PATH in flattened
    assert ACTION_OCR_ADB_SCREENSHOT in flattened
    assert "a" * 32 in flattened
