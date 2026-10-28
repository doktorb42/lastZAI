"""Zgodność dawnej nazwy detektora po przeniesieniu do Oczu."""

from __future__ import annotations

import unittest

import oczy.screen_detection as legacy
from oczy import screen_detection as eyes


class ScreenDetectionCompatibilityTests(unittest.TestCase):
    def test_old_and_new_paths_share_the_detector_functions(self) -> None:
        self.assertIs(legacy.classify_screen, eyes.classify_screen)
        self.assertIs(legacy.detect_unread_messages, eyes.detect_unread_messages)
        self.assertIs(legacy.has_private_unread_badge, eyes.has_private_unread_badge)


if __name__ == "__main__":
    unittest.main()
