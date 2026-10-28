"""Duża pętla starego runtime ma jedno źródło poza katalogiem głównym."""

from __future__ import annotations

import unittest

import archive.legacy_runtime.player_loop as legacy
from archive.legacy_runtime import player_loop


class LegacyPlayerLoopBridgeTests(unittest.TestCase):
    def test_bridge_exposes_the_archived_player_loop(self) -> None:
        self.assertIs(legacy.PlayerLoopConfig, player_loop.PlayerLoopConfig)
        self.assertIs(legacy.PrivateReplySafety, player_loop.PrivateReplySafety)
        self.assertIs(legacy.PlayerLoopRecorder, player_loop.PlayerLoopRecorder)
        self.assertIs(legacy.PlayerLoop, player_loop.PlayerLoop)

    def test_bridge_exposes_archived_helpers_without_copying_them(self) -> None:
        self.assertIs(
            legacy._style_candidate_from_text,
            player_loop._style_candidate_from_text,
        )
        candidate = legacy._style_candidate_from_text("be more playful")
        self.assertIsInstance(candidate, dict)
        self.assertEqual(candidate.get("tags"), ["playful"])


if __name__ == "__main__":
    unittest.main()
