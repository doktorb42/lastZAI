"""Osobne okno Oczu jest dodatkiem testowym, nie drugim runtime."""

from __future__ import annotations

import unittest

import oczy.eyes_window as legacy
from oczy import eyes_window as active


class EyesWindowCompatibilityTests(unittest.TestCase):
    def test_old_and_new_paths_share_the_same_window_classes(self) -> None:
        self.assertIs(legacy.EyesWatchWorker, active.EyesWatchWorker)
        self.assertIs(legacy.PEvaEyesWindow, active.PEvaEyesWindow)


if __name__ == "__main__":
    unittest.main()
