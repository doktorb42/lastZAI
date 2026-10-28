from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mozg.llama_server_manager import LlamaServerManager, start_configured_server
from main import _settings_recorder
from config.runtime_settings import (
    LlamaServerSettings,
    SettingsError,
    load_model_registry,
    load_settings,
    render_effective_settings,
    resolve_model_for_role,
)
from archive.legacy_runtime.settings_menu import run_settings_menu


class _Process:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9


class RuntimeSettingsTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "config" / "profiles").mkdir(parents=True)
        return temp, root

    @staticmethod
    def write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def write_registry(self, root: Path) -> Path:
        model = root / "models" / "chat.gguf"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"GGUF")
        self.write_json(root / "config" / "models.json", {
            "models": {
                "chat": {"name": "Chat", "path": "models/chat.gguf", "role": "chat"},
                "action": {"name": "Action", "path": "models/chat.gguf", "role": "action"},
            }
        })
        return model

    def test_missing_file_uses_safe_defaults_and_auto_send_is_false(self) -> None:
        temp, root = self.make_root()
        with temp:
            settings = load_settings(project_root=root)
            self.assertFalse(settings.runtime.auto_send)
            self.assertTrue(settings.runtime.player_loop)
            self.assertEqual(settings.navigation.ui_effect_poll_ms, 200)

    def test_settings_file_overrides_defaults(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_json(root / "config" / "settings.json", {
                "runtime": {"social_debug": True},
                "navigation": {"ui_effect_poll_ms": 250},
            })
            settings = load_settings(project_root=root)
            self.assertTrue(settings.runtime.social_debug)
            self.assertEqual(settings.navigation.ui_effect_poll_ms, 250)
            self.assertFalse(settings.runtime.auto_send)

    def test_agency_social_preview_fallback_can_be_disabled_in_settings(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_json(root / "config" / "settings.json", {
                "agency": {"social_preview_fallback": False},
            })
            settings = load_settings(project_root=root)
            self.assertFalse(settings.agency.social_preview_fallback)
            self.assertIn('"social_preview_fallback": false', render_effective_settings(settings))

    def test_cli_overrides_settings_file(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_json(root / "config" / "settings.json", {"runtime": {"auto_send": False}})
            settings = load_settings(
                project_root=root,
                cli_overrides={"runtime": {"auto_send": True}},
            )
            self.assertTrue(settings.runtime.auto_send)

    def test_invalid_json_fails_with_clear_location(self) -> None:
        temp, root = self.make_root()
        with temp:
            path = root / "config" / "settings.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(SettingsError, "Invalid JSON.*line"):
                load_settings(project_root=root)

    def test_profile_overrides_base_settings(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_json(root / "config" / "settings.json", {"runtime": {"auto_send": False}})
            self.write_json(root / "config" / "profiles" / "private-auto.json", {
                "runtime": {"auto_send": True, "private_only": True}
            })
            settings = load_settings(project_root=root, profile="private-auto")
            self.assertEqual(settings.profile, "private-auto")
            self.assertTrue(settings.runtime.auto_send)

    def test_registry_resolves_project_relative_path_and_custom_path(self) -> None:
        temp, root = self.make_root()
        with temp:
            model = self.write_registry(root)
            self.write_json(root / "config" / "settings.json", {
                "llm": {"chat_model": {"model_id": "chat"}}
            })
            settings = load_settings(project_root=root)
            registry = load_model_registry(project_root=root)
            self.assertEqual(resolve_model_for_role(settings, registry).path, model.resolve())
            custom = root / "custom.gguf"
            custom.write_bytes(b"GGUF")
            self.write_json(root / "config" / "settings.json", {
                "llm": {"chat_model": {"model_id": "", "path": "custom.gguf"}}
            })
            direct = load_settings(project_root=root)
            self.assertEqual(resolve_model_for_role(direct, registry).path, custom.resolve())

    def test_action_only_model_is_rejected_for_chat(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_registry(root)
            self.write_json(root / "config" / "settings.json", {
                "llm": {"chat_model": {"model_id": "action"}}
            })
            settings = load_settings(project_root=root)
            with self.assertRaisesRegex(SettingsError, "declared role='action'"):
                resolve_model_for_role(settings, load_model_registry(project_root=root))

    def test_invalid_port_and_navigation_bounds_are_rejected(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_json(root / "config" / "settings.json", {"llm": {"port": 70000}})
            with self.assertRaisesRegex(SettingsError, "llm.port"):
                load_settings(project_root=root)
            self.write_json(root / "config" / "settings.json", {
                "navigation": {"ui_effect_poll_ms": 0}
            })
            with self.assertRaisesRegex(SettingsError, "ui_effect_poll_ms"):
                load_settings(project_root=root)

    def test_remote_llm_host_is_rejected_to_keep_chat_context_local(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_json(root / "config" / "settings.json", {"llm": {"host": "model.example"}})
            with self.assertRaisesRegex(SettingsError, "local loopback"):
                load_settings(project_root=root)

    def test_show_settings_output_is_effective_and_secret_free(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_registry(root)
            self.write_json(root / "config" / "settings.json", {"llm": {"chat_model": {"model_id": "chat"}}})
            output = render_effective_settings(
                load_settings(project_root=root), load_model_registry(project_root=root)
            )
            self.assertIn('"selected_chat_model"', output)
            self.assertNotIn("api_key", output.lower())

    def test_recording_settings_snapshot_is_secret_free(self) -> None:
        temp, root = self.make_root()
        with temp:
            settings = load_settings(project_root=root)
            session_root = root / "recordings"
            with patch("main.DATA_DIR", session_root):
                _settings_recorder(settings)
            snapshots = list(session_root.glob("player-loop/session-*/settings.json"))
            self.assertEqual(len(snapshots), 1)
            self.assertNotIn("api_key", snapshots[0].read_text(encoding="utf-8").lower())

    def test_interactive_save_and_load(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_registry(root)
            path = root / "config" / "settings.json"
            settings = load_settings(project_root=root)
            answers = iter(["2", "y", "9", "0"])
            run_settings_menu(
                settings,
                load_model_registry(project_root=root),
                input_func=lambda _prompt: next(answers),
                output=lambda _line: None,
            )
            self.assertTrue(load_settings(project_root=root).runtime.auto_send)
            self.assertTrue(path.is_file())

    def test_existing_healthy_server_is_reused_and_never_terminated(self) -> None:
        temp, root = self.make_root()
        with temp:
            model = self.write_registry(root)
            self.write_json(root / "config" / "settings.json", {"llm": {"chat_model": {"model_id": "chat"}}})
            settings = load_settings(project_root=root)
            resolved = resolve_model_for_role(settings, load_model_registry(project_root=root))
            process = _Process()
            manager = LlamaServerManager(
                settings, resolved, health_check=lambda: True,
                process_factory=lambda *_args, **_kwargs: process,
            )
            self.assertFalse(manager.ensure_running())
            manager.close()
            self.assertEqual(process.terminated, 0)
            self.assertEqual(resolved.path, model.resolve())

    def test_auto_start_disabled_preserves_external_server_behavior(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_registry(root)
            self.write_json(root / "config" / "settings.json", {
                "llm": {"chat_model": {"model_id": "chat"}, "auto_start_server": False}
            })
            settings = load_settings(project_root=root)
            resolved = resolve_model_for_role(settings, load_model_registry(project_root=root))
            with patch("mozg.llama_server_manager.LlamaServerManager") as manager:
                self.assertIsNone(start_configured_server(settings, resolved))
            manager.assert_not_called()

    def test_peva_owned_server_is_terminated_when_configured(self) -> None:
        temp, root = self.make_root()
        with temp:
            self.write_registry(root)
            executable = root / "llama-server.exe"
            executable.write_bytes(b"exe")
            self.write_json(root / "config" / "settings.json", {
                "llm": {"auto_start_server": True, "chat_model": {"model_id": "chat"}},
                "llama_server": {"executable": "llama-server.exe"},
            })
            settings = load_settings(project_root=root)
            resolved = resolve_model_for_role(settings, load_model_registry(project_root=root))
            process = _Process()
            health = iter([False, True])
            manager = LlamaServerManager(
                settings, resolved, health_check=lambda: next(health),
                process_factory=lambda *_args, **_kwargs: process,
                sleep=lambda _seconds: None,
            )
            with patch("mozg.llama_server_manager._port_is_open", return_value=False):
                self.assertTrue(manager.ensure_running(timeout_seconds=1))
            self.assertTrue(manager.managed)
            manager.close()
            self.assertEqual(process.terminated, 1)


if __name__ == "__main__":
    unittest.main()
