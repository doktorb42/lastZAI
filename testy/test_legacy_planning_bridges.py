"""Planowanie starego runtime ma jedno źródło poza katalogiem głównym."""

from __future__ import annotations

import unittest

import archive.legacy_runtime.decision_engine as legacy_decisions
import archive.legacy_runtime.navigation_manager as legacy_navigation
from archive.legacy_runtime import decision_engine, navigation_manager


class LegacyPlanningBridgeTests(unittest.TestCase):
    def test_decision_bridge_uses_the_archived_implementation(self) -> None:
        self.assertIs(legacy_decisions.DecisionEngine, decision_engine.DecisionEngine)
        self.assertIs(legacy_decisions.DecisionType, decision_engine.DecisionType)

    def test_navigation_bridge_uses_the_archived_implementation(self) -> None:
        self.assertIs(legacy_navigation.NavigationManager, navigation_manager.NavigationManager)
        self.assertIs(legacy_navigation.NAVIGATION_GRAPH, navigation_manager.NAVIGATION_GRAPH)


if __name__ == "__main__":
    unittest.main()
