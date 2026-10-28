"""Wykonawca starego runtime ma jedno źródło poza katalogiem głównym."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import archive.legacy_runtime.action_ex as legacy
from archive.legacy_runtime import action_ex


class LegacyActionExecutorBridgeTests(unittest.TestCase):
    def test_bridge_exposes_the_archived_executor(self) -> None:
        self.assertIs(legacy.ActionPolicy, action_ex.ActionPolicy)
        self.assertIs(legacy.ActionResult, action_ex.ActionResult)
        self.assertIs(legacy.ActionExecutor, action_ex.ActionExecutor)
        self.assertIs(legacy.build_private_reply_action, action_ex.build_private_reply_action)

    def test_bridge_forwards_temporary_configuration_overrides(self) -> None:
        original = action_ex.DEFAULT_MAX_MESSAGE_CHARS
        with patch.object(legacy, "DEFAULT_MAX_MESSAGE_CHARS", 17):
            self.assertEqual(action_ex.DEFAULT_MAX_MESSAGE_CHARS, 17)
        self.assertEqual(action_ex.DEFAULT_MAX_MESSAGE_CHARS, original)


if __name__ == "__main__":
    unittest.main()
