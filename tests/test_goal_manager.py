from __future__ import annotations

import unittest

from archive.legacy_runtime.goal_manager import Goal, GoalManager, GoalPriority, GoalStatus, GoalType


def pm_goal(seq: int, *, priority: GoalPriority = GoalPriority.HIGH) -> Goal:
    return Goal(
        goal_type=GoalType.ANSWER_PRIVATE_MESSAGE,
        priority=priority,
        payload={
            "dedupe_key": "pm:custom_1_2_v2:1",
            "room_id": "custom_1_2_v2",
            "sender_uid": "1",
            "messages": [{"source_message_key": f"custom_1_2_v2:{seq}", "seq_id": seq}],
        },
    )


class GoalManagerTests(unittest.TestCase):
    def test_private_message_goals_merge_by_room_and_sender(self) -> None:
        manager = GoalManager()
        first = manager.add_goal(pm_goal(1, priority=GoalPriority.NORMAL))
        for seq in range(2, 6):
            merged = manager.add_goal(pm_goal(seq, priority=GoalPriority.HIGH))
            self.assertEqual(merged.goal_id, first.goal_id)

        goals = manager.snapshot()
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["priority"], int(GoalPriority.HIGH))
        self.assertEqual(len(goals[0]["payload"]["messages"]), 5)

    def test_merge_retains_each_pending_command_and_memory_candidate(self) -> None:
        manager = GoalManager()
        first = pm_goal(1)
        first.payload.update({
            "parsed_command": {"command_type": "REINFORCE_PLAYER", "risk_level": "MEDIUM"},
            "parsed_commands": [{"command_type": "REINFORCE_PLAYER", "risk_level": "MEDIUM"}],
            "pending_commands": [{"command_id": "c1", "command_type": "REINFORCE_PLAYER"}],
            "memory_candidates": [{"candidate_id": "m1", "value": "Alex"}],
        })
        manager.add_goal(first)
        later = pm_goal(2)
        later.payload.update({
            "parsed_command": None,
            "parsed_commands": [{"command_type": "WAIT", "risk_level": "LOW"}],
            "pending_commands": [{"command_id": "c2", "command_type": "WAIT"}],
            "memory_candidates": [{"candidate_id": "m2", "value": "riders"}],
        })
        goal = manager.add_goal(later)
        self.assertEqual(goal.payload["parsed_command"]["command_type"], "REINFORCE_PLAYER")
        self.assertEqual(len(goal.payload["parsed_commands"]), 2)
        self.assertEqual(len(goal.payload["pending_commands"]), 2)
        self.assertEqual(len(goal.payload["memory_candidates"]), 2)

    def test_lifecycle_and_retry_limit(self) -> None:
        manager = GoalManager()
        goal = manager.add_goal(
            Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT, max_attempts=2)
        )
        self.assertEqual(manager.get_next_goal().goal_id, goal.goal_id)

        manager.activate_goal(goal.goal_id)
        retry = manager.fail_goal(goal.goal_id, reason="temporary", retry=True)
        self.assertEqual(retry.status, GoalStatus.PENDING)
        self.assertEqual(retry.attempts, 1)

        manager.activate_goal(goal.goal_id)
        failed = manager.fail_goal(goal.goal_id, reason="second failure", retry=True)
        self.assertEqual(failed.status, GoalStatus.FAILED)
        self.assertEqual(failed.attempts, 2)

    def test_priority_and_terminal_lifecycle(self) -> None:
        manager = GoalManager()
        low = manager.add_goal(Goal(goal_type=GoalType.WAIT, priority=GoalPriority.LOW))
        high = manager.add_goal(
            Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT, priority=GoalPriority.HIGH)
        )
        self.assertEqual(manager.get_next_goal().goal_id, high.goal_id)
        self.assertEqual(manager.complete_goal(high.goal_id).status, GoalStatus.COMPLETED)
        self.assertEqual(manager.get_next_goal().goal_id, low.goal_id)
        self.assertEqual(manager.cancel_goal(low.goal_id).status, GoalStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
