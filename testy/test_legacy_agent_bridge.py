"""Agent zachowanego runtime ma jedno źródło poza katalogiem głównym."""

from __future__ import annotations

import unittest

import archive.legacy_runtime.agent as legacy
from archive.legacy_runtime import agent


class LegacyAgentBridgeTests(unittest.TestCase):
    def test_bridge_uses_the_archived_agent_implementation(self) -> None:
        self.assertIs(legacy.AgentConfig, agent.AgentConfig)
        self.assertIs(legacy.AgentResult, agent.AgentResult)
        self.assertIs(legacy.PendingReply, agent.PendingReply)
        self.assertIs(legacy.LastZAgent, agent.LastZAgent)

    def test_bridge_preserves_the_legacy_defaults(self) -> None:
        self.assertEqual(legacy.DEFAULT_OWN_UID, agent.DEFAULT_OWN_UID)
        self.assertEqual(legacy.DEFAULT_DB_PATH, agent.DEFAULT_DB_PATH)


if __name__ == "__main__":
    unittest.main()
