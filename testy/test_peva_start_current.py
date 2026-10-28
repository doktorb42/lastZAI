"""Test zgodności zachowanego, starego launchera capture — bez ADB ani gry.

Normalny start PEva jest testowany osobno przez ``main.py`` i ``peva_app.py``.
"""

from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import archive.legacy_runtime.peva_start as peva_start
class LegacyPevaStartCompatibilityTests(unittest.TestCase):
    def test_legacy_launcher_uses_status_then_starts_only_simulated_capture_test(self) -> None:
        status = {"patch_present_in_asset_bundle": True, "locations": {}}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_path = root / "peva_chat_test.json"

            def simulated_listen(_adb: str, _device: str, report: dict[str, object]) -> None:
                report["status"] = "PASS"
                report["detail"] = "simulated"

            with (
                patch.object(peva_start, "find_adb", return_value="fake-adb"),
                patch.object(peva_start, "find_device", return_value=("fake-device", 3161)),
                patch.object(peva_start, "patch_command", return_value=(status, "")) as patch_command,
                patch.object(peva_start, "start_game") as start_game,
                patch.object(peva_start, "listen", side_effect=simulated_listen) as listen,
                patch.object(peva_start.time, "sleep"),
                patch.object(peva_start, "REPORT_PATH", report_path),
                patch.object(peva_start, "PRE_STATUS_LOG", root / "pre.log"),
                patch.object(peva_start, "POST_STATUS_LOG", root / "post.log"),
                patch.object(peva_start, "PATCH_LOG", root / "patch.log"),
            ):
                # The preserved compatibility launcher prints a human-facing
                # progress trace.  Keep test runs quiet so that this simulated
                # capture cannot be mistaken for a real game/capture session.
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(peva_start.main(), 0)

            self.assertEqual([call.args[0] for call in patch_command.call_args_list], ["status", "status"])
            start_game.assert_called_once_with("fake-adb", "fake-device")
            listen.assert_called_once()
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
