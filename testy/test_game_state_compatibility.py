"""Dawna nazwa skrótu stanu ekranu nadal wskazuje Oczy."""

from __future__ import annotations

import unittest

import oczy.game_state as legacy
from oczy import game_state as eyes


class GameStateCompatibilityTests(unittest.TestCase):
    def test_old_and_new_paths_share_the_same_public_function(self) -> None:
        self.assertIs(legacy.get_game_state, eyes.get_game_state)


if __name__ == "__main__":
    unittest.main()
