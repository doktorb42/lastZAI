"""Nowe okno PEva musi dać się zaimportować bez tworzenia GUI ani GPU."""

from __future__ import annotations

import threading
import unittest


class PEvaAppTests(unittest.TestCase):
    def test_window_module_imports_without_starting_runtime(self) -> None:
        import peva_app

        self.assertTrue(callable(peva_app.main))
        self.assertTrue(hasattr(peva_app, "PEvaWindow"))

    def test_closing_the_normal_window_closes_panel_and_runtime_exactly_once(self) -> None:
        import peva_app

        class Root:
            destroyed = 0

            def destroy(self) -> None:
                self.destroyed += 1

        class Detail:
            values: list[str] = []

            def set(self, value: str) -> None:
                self.values.append(value)

        class Panel:
            closed = 0

            def close(self) -> None:
                self.closed += 1

        class Runtime:
            closed = 0

            def close(self) -> None:
                self.closed += 1

        root, detail, panel, runtime = Root(), Detail(), Panel(), Runtime()
        window = peva_app.PEvaWindow.__new__(peva_app.PEvaWindow)
        window.root = root
        window.detail = detail
        window.panel = panel
        window.runtime = runtime
        window._closing = threading.Event()

        window.close()
        window.close()

        self.assertEqual(panel.closed, 1)
        self.assertEqual(runtime.closed, 1)
        self.assertEqual(root.destroyed, 1)
        self.assertTrue(window._closing.is_set())
        self.assertEqual(detail.values, ["Zatrzymuję PEvę…"])


if __name__ == "__main__":
    unittest.main()
