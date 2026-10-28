"""Styl, relacje, naturalność i czas odpowiedzi są w swoich modułach PEva."""

from __future__ import annotations

import unittest

import charakter.player_style as legacy_style
import mozg.reply_naturalness as legacy_naturalness
import emocje.response_delay as legacy_delay
import charakter.social_context as legacy_social
from charakter import player_style as active_style
from charakter import social_context as active_social
from emocje import response_delay as active_delay
from mozg import reply_naturalness as active_naturalness


class SocialModuleCompatibilityTests(unittest.TestCase):
    def test_legacy_and_active_modules_share_public_types(self) -> None:
        self.assertIs(legacy_style.PlayerStyle, active_style.PlayerStyle)
        self.assertIs(legacy_social.PlayerProfile, active_social.PlayerProfile)
        self.assertIs(legacy_naturalness.ReplyNaturalness, active_naturalness.ReplyNaturalness)
        self.assertIs(legacy_delay.ResponseDelayManager, active_delay.ResponseDelayManager)


if __name__ == "__main__":
    unittest.main()
