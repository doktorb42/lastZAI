"""Stary stan i kolejki mają jedno źródło w archiwalnym runtime."""

from __future__ import annotations

import unittest

import archive.legacy_runtime.goal_manager as legacy_goals
import archive.legacy_runtime.user_decision as legacy_decisions
import archive.legacy_runtime.world_state as legacy_state
from archive.legacy_runtime import goal_manager, user_decision, world_state


class LegacyStateBridgeTests(unittest.TestCase):
    def test_world_state_bridge_uses_the_archived_implementation(self) -> None:
        self.assertIs(legacy_state.WorldState, world_state.WorldState)
        self.assertIs(legacy_state.KNOWN_SCREENS, world_state.KNOWN_SCREENS)

    def test_goal_bridge_uses_the_archived_implementation(self) -> None:
        self.assertIs(legacy_goals.Goal, goal_manager.Goal)
        self.assertIs(legacy_goals.GoalManager, goal_manager.GoalManager)
        self.assertIs(legacy_goals.GoalType, goal_manager.GoalType)

    def test_decision_bridge_uses_the_archived_implementation(self) -> None:
        self.assertIs(legacy_decisions.UserDecisionManager, user_decision.UserDecisionManager)
        self.assertIs(legacy_decisions.TerminalDecisionControl, user_decision.TerminalDecisionControl)


if __name__ == "__main__":
    unittest.main()
