"""Krótkie mostki nie mogą ponownie stać się drugą implementacją."""

from __future__ import annotations

import unittest

import archive.legacy_runtime.message_logging as legacy_logging
import archive.legacy_runtime.player_events as legacy_events
import archive.legacy_runtime.settings_menu as legacy_settings
from archive.legacy_runtime import message_logging, player_events, settings_menu


class LegacyRuntimeBridgeTests(unittest.TestCase):
    def test_logging_bridge_uses_the_archived_implementation(self) -> None:
        self.assertIs(legacy_logging.incoming_message, message_logging.incoming_message)
        self.assertIs(legacy_logging.send_confirmed, message_logging.send_confirmed)

    def test_event_bridge_uses_the_archived_implementation(self) -> None:
        self.assertIs(legacy_events.EventType, player_events.EventType)
        self.assertIs(legacy_events.PlayerEvent, player_events.PlayerEvent)

    def test_settings_bridge_uses_the_archived_implementation(self) -> None:
        self.assertIs(legacy_settings.run_settings_menu, settings_menu.run_settings_menu)


if __name__ == "__main__":
    unittest.main()
