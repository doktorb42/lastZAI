"""Preflight modelu nie może uruchomić GPU ani serwera."""

from __future__ import annotations

import unittest

from mozg.local_model import LocalModel


class LocalModelTests(unittest.TestCase):
    def test_preflight_reports_configured_local_model_without_starting_it(self) -> None:
        model = LocalModel()

        status = model.preflight()

        self.assertTrue(status.configured)
        self.assertFalse(status.ready)
        self.assertTrue(status.model_path)

    def test_repeated_start_keeps_original_owned_manager(self) -> None:
        class Manager:
            managed = True

            def __init__(self) -> None:
                self.starts = 0
                self.closes = 0

            def ensure_running(self) -> bool:
                self.starts += 1
                return True

            def close(self, *, force: bool = False) -> None:
                self.closes += 1

        class HealthyAI:
            def __init__(self, **kwargs: object) -> None:
                pass

            def health_check(self) -> bool:
                return True

        manager = Manager()
        model = LocalModel(
            ai_factory=HealthyAI,  # type: ignore[arg-type]
            manager_factory=lambda _settings, _resolved: manager,  # type: ignore[arg-type]
        )

        first = model.start()
        second = model.start()
        model.close()

        self.assertTrue(first.ready)
        self.assertTrue(second.ready)
        self.assertEqual(manager.starts, 1)
        self.assertEqual(manager.closes, 1)

    def test_failed_client_health_closes_newly_owned_manager(self) -> None:
        class Manager:
            managed = True

            def __init__(self) -> None:
                self.closes = 0

            def ensure_running(self) -> bool:
                return True

            def close(self, *, force: bool = False) -> None:
                self.closes += 1

        class UnhealthyAI:
            def __init__(self, **kwargs: object) -> None:
                pass

            def health_check(self) -> bool:
                return False

        manager = Manager()
        model = LocalModel(
            ai_factory=UnhealthyAI,  # type: ignore[arg-type]
            manager_factory=lambda _settings, _resolved: manager,  # type: ignore[arg-type]
        )

        status = model.start()

        self.assertFalse(status.ready)
        self.assertEqual(manager.closes, 1)


if __name__ == "__main__":
    unittest.main()
