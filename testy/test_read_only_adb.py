"""Adapter Rąk ma tylko dwa jawne, testowalne gesty."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from config.runtime_config import ADB_DEVICE, ADB_PATH
from rece.read_only_adb import swipe, tap


class ReadOnlyAdbTests(unittest.TestCase):
    def test_tap_uses_only_the_android_input_tap_command(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def runner(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0)

        self.assertTrue(tap(123, 456, runner=runner))
        self.assertEqual(
            calls,
            [
                (
                    (ADB_PATH, "-s", ADB_DEVICE, "shell", "input", "tap", "123", "456"),
                    {"capture_output": True, "text": False, "timeout": 5.0},
                )
            ],
        )

    def test_swipe_uses_only_the_android_input_swipe_command(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def runner(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0)

        self.assertTrue(swipe(10, 20, 30, 40, 260, runner=runner))
        self.assertEqual(
            calls[0][0],
            (ADB_PATH, "-s", ADB_DEVICE, "shell", "input", "swipe", "10", "20", "30", "40", "260"),
        )

    def test_invalid_coordinates_make_no_adb_call(self) -> None:
        self.assertFalse(tap(0, 10, runner=lambda *_args, **_kwargs: self.fail("must not run")))
        self.assertFalse(swipe(1, 2, 3, 4, 0, runner=lambda *_args, **_kwargs: self.fail("must not run")))


if __name__ == "__main__":
    unittest.main()
