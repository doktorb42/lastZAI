"""Adapter zrzutu Oczu ma wyłącznie kontrakt odczytu jednego PNG."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from config.runtime_config import ADB_DEVICE, ADB_PATH
from oczy import vision
from oczy.screen_capture import capture_frame, capture_png_bytes


class EyesScreenCaptureTests(unittest.TestCase):
    def test_vision_keeps_the_existing_capture_entry_point(self) -> None:
        self.assertIs(vision.capture_frame, capture_frame)

    def test_requests_only_an_in_memory_screencap(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def runner(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=b"png-bytes")

        self.assertEqual(capture_png_bytes(runner=runner), b"png-bytes")
        self.assertEqual(
            calls,
            [
                (
                    (ADB_PATH, "-s", ADB_DEVICE, "exec-out", "screencap", "-p"),
                    {"capture_output": True, "text": False, "timeout": 15.0},
                )
            ],
        )

    def test_failed_capture_does_not_return_partial_data(self) -> None:
        def runner(*_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=1, stdout=b"partial")

        with self.assertRaisesRegex(RuntimeError, "could not capture"):
            capture_png_bytes(runner=runner)


if __name__ == "__main__":
    unittest.main()
