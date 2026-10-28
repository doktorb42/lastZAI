"""Nowy runtime PEva nie może przypadkiem wrócić do starego transportu gry."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_IMPORTS = {
    "adb_controll",
    "action_ex",
    "agent",
    "chat_capture",
    "decision_engine",
    "goal_manager",
    "player_loop",
    "navigation_manager",
    "private_chat_navigation",
    "user_decision",
    "world_state",
    "trening",
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    class RuntimeImports(ast.NodeVisitor):
        def __init__(self) -> None:
            self.modules: set[str] = set()
            self._type_checking_depth = 0

        def visit_If(self, node: ast.If) -> None:  # noqa: N802
            is_type_checking = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
            if is_type_checking:
                self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            if is_type_checking:
                self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            if not self._type_checking_depth:
                self.modules.update(alias.name.split(".", 1)[0] for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            if not self._type_checking_depth and node.module:
                self.modules.add(node.module.lstrip(".").split(".", 1)[0])

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            # Local imports used only by an explicit manual self-test do not
            # take part in normal PEva startup.
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            return

    visitor = RuntimeImports()
    visitor.visit(tree)
    return visitor.modules


class NewRuntimeBoundaryTests(unittest.TestCase):
    def test_normal_start_and_reasoning_layers_do_not_import_legacy_or_training(self) -> None:
        files = [
            ROOT / "main.py",
            ROOT / "peva_app.py",
            ROOT / "peva_panel.py",
            ROOT / "peva_runtime.py",
            *(ROOT / "mozg").glob("*.py"),
            *(ROOT / "charakter").glob("*.py"),
            *(ROOT / "emocje").glob("*.py"),
        ]
        for path in files:
            with self.subTest(path=path.name):
                self.assertFalse(
                    imported_modules(path) & FORBIDDEN_RUNTIME_IMPORTS,
                    f"{path.name} must not reconnect the new runtime to legacy control or training modules",
                )

    def test_read_only_hands_have_no_text_entry_or_send_symbols(self) -> None:
        files = [
            ROOT / "rece" / "read_only_navigation.py",
            ROOT / "rece" / "read_only_adb.py",
        ]
        forbidden = ("input_text", "write_message", "send_message", "send_private_reply_verified")
        for path in files:
            source = path.read_text(encoding="utf-8")
            for name in forbidden:
                with self.subTest(path=path.name, name=name):
                    self.assertNotIn(name, source)

    def test_eyes_and_read_only_hands_do_not_depend_on_legacy_adb(self) -> None:
        files = [
            ROOT / "oczy" / "android_capture.py",
            ROOT / "oczy" / "screen_capture.py",
            ROOT / "rece" / "read_only_adb.py",
            ROOT / "rece" / "read_only_navigation.py",
        ]
        for path in files:
            with self.subTest(path=path.name):
                self.assertNotIn("adb_controll", path.read_text(encoding="utf-8"))

    def test_normal_click_entry_starts_only_main_python_file(self) -> None:
        command = (ROOT / "PEva.cmd").read_text(encoding="utf-8").casefold()

        self.assertIn("main.py", command)
        self.assertNotIn("archive.legacy_runtime.player_loop.py", command)
        self.assertNotIn("archive.legacy_runtime.action_ex.py", command)
        self.assertNotIn("archive.legacy_runtime.peva_start.py", command)

    def test_root_has_one_normal_click_launcher(self) -> None:
        launchers = sorted(
            path.name for path in ROOT.iterdir() if path.is_file() and path.suffix.casefold() in {".cmd", ".bat"}
        )
        self.assertEqual(launchers, ["PEva.cmd"])

    def test_root_has_only_the_normal_runtime_python_files(self) -> None:
        python_files = sorted(path.name for path in ROOT.glob("*.py"))
        self.assertEqual(
            python_files,
            ["contracts.py", "main.py", "peva_app.py", "peva_panel.py", "peva_runtime.py"],
        )

    def test_root_has_no_historical_wsl_runner(self) -> None:
        historical_runners = sorted(path.name for path in ROOT.glob("run_peva_v*_*.sh"))
        self.assertEqual(historical_runners, [])


if __name__ == "__main__":
    unittest.main()
