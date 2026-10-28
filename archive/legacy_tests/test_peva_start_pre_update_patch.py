from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import peva_start


class PevaStartTests(unittest.TestCase):
    def test_recorded_version_skips_repatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"schema": peva_start.PATCH_STATE_SCHEMA, "game_version": 3161}), encoding="utf-8")
            called = False

            def fake_run(*_args, **_kwargs):
                nonlocal called
                called = True
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            self.assertEqual(peva_start.ensure_chat_capture(3161, run=fake_run, state_path=state, log_dir=Path(tmp) / "logs"), "verified")
            self.assertFalse(called)

    def test_patch_success_records_current_game_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(*_args, **_kwargs):
                return SimpleNamespace(returncode=0, stdout="PATCH_ALREADY_INSTALLED", stderr="")

            result = peva_start.ensure_chat_capture(3161, run=fake_run, state_path=root / "state.json", log_dir=root / "logs")
            self.assertEqual(result, "already_installed")
            self.assertEqual(peva_start.recorded_patch_version(root / "state.json"), 3161)
            payload = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], peva_start.PATCH_STATE_SCHEMA)

    def test_failed_patch_never_records_a_false_ready_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(*_args, **_kwargs):
                return SimpleNamespace(returncode=1, stdout="", stderr="failed")

            with self.assertRaises(peva_start.LauncherError):
                peva_start.ensure_chat_capture(3161, run=fake_run, state_path=root / "state.json", log_dir=root / "logs")
            self.assertIsNone(peva_start.recorded_patch_version(root / "state.json"))

    def test_main_only_verifies_patch_and_does_not_start_runtime(self) -> None:
        with (
            patch.object(peva_start, "installed_game_version", return_value=3161),
            patch.object(peva_start, "ensure_chat_capture", return_value="verified") as ensure,
        ):
            self.assertEqual(peva_start.main(), 0)
        ensure.assert_called_once_with(3161)


if __name__ == "__main__":
    unittest.main()
