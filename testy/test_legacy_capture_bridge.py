"""Capture starego runtime ma jedno źródło poza katalogiem głównym."""

from __future__ import annotations

import unittest

import archive.legacy_runtime.chat_capture as legacy
from archive.legacy_runtime import chat_capture


class LegacyCaptureBridgeTests(unittest.TestCase):
    def test_bridge_uses_the_archived_capture_implementation(self) -> None:
        self.assertIs(legacy.ChatCapture, chat_capture.ChatCapture)
        self.assertIs(legacy.ChatCaptureConfig, chat_capture.ChatCaptureConfig)
        self.assertIs(legacy.ChatMessage, chat_capture.ChatMessage)

    def test_bridge_preserves_capture_constants(self) -> None:
        self.assertIs(legacy.CHAT_PUSH_COMMANDS, chat_capture.CHAT_PUSH_COMMANDS)
        self.assertEqual(legacy.DEFAULT_ADB_PATH, chat_capture.DEFAULT_ADB_PATH)
        self.assertEqual(legacy.DEFAULT_DEVICE, chat_capture.DEFAULT_DEVICE)
        self.assertEqual(legacy.DEFAULT_SELF_UID, chat_capture.DEFAULT_SELF_UID)


if __name__ == "__main__":
    unittest.main()
