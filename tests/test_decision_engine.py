from __future__ import annotations

import unittest

from archive.legacy_runtime.decision_engine import DecisionEngine, DecisionType
from archive.legacy_runtime.goal_manager import Goal, GoalType
from archive.legacy_runtime.world_state import WorldState


class DecisionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DecisionEngine()

    def test_no_goal_waits_without_clicking(self) -> None:
        decision = self.engine.decide(WorldState(), None, {})
        self.assertEqual(decision.decision_type, DecisionType.WAIT)
        self.assertIsNone(decision.action)

    def test_unknown_screen_blocks_navigation(self) -> None:
        state = WorldState(adb_connected=True)
        state.update_screen("UNKNOWN")
        goal = Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT)

        decision = self.engine.decide(state, goal, {})

        self.assertEqual(decision.decision_type, DecisionType.WAIT)
        self.assertIsNone(decision.action)

    def test_known_hq_can_open_chat_deterministically(self) -> None:
        state = WorldState(adb_connected=True)
        state.update_screen("HQ_BASE")
        goal = Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT)

        decision = self.engine.decide(state, goal, {})

        self.assertEqual(decision.decision_type, DecisionType.OPEN_CHAT)
        self.assertEqual(decision.action, {"action": "open_chat"})

    def test_global_waiting_flag_does_not_emit_ask_user_loop(self) -> None:
        state = WorldState(adb_connected=True, waiting_for_user=True)
        state.update_screen("HQ_BASE")
        goal = Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT)

        decision = self.engine.decide(state, goal, {})

        self.assertEqual(decision.decision_type, DecisionType.OPEN_CHAT)

    def test_send_lane_pause_returns_wait_without_asking_again(self) -> None:
        state = WorldState(adb_connected=True)
        state.update_screen("PRIVATE_CHAT")
        goal = Goal(goal_type=GoalType.ANSWER_PRIVATE_MESSAGE)

        decision = self.engine.decide(
            state,
            goal,
            {"auto_send": True, "send_lane_blocked": True},
        )

        self.assertEqual(decision.decision_type, DecisionType.WAIT)
        self.assertEqual(decision.wait_state, "WAITING_FOR_USER_DECISION")


if __name__ == "__main__":
    unittest.main()
