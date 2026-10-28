from __future__ import annotations

"""Model, chaos, and real-trace regression coverage for NavigationManager."""

import unittest
import random
from types import SimpleNamespace
from unittest.mock import patch

from archive.legacy_runtime.action_ex import ActionExecutor
from archive.legacy_runtime.chat_capture import ChatMessage
from archive.legacy_runtime.goal_manager import Goal, GoalStatus, GoalType
from archive.legacy_runtime.navigation_manager import NAVIGATION_GRAPH, NavigationManager, NavigationStatus
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig
from oczy.screen_detection import classify_screen
from archive.legacy_runtime.world_state import WorldState


SELF_UID = "1224463480000772"
PEER_UID = "1349515331000772"
OTHER_UID = "998877"
ROOM_ID = f"custom_{PEER_UID}_{SELF_UID}_v2"
OTHER_ROOM_ID = f"custom_{OTHER_UID}_{SELF_UID}_v2"


def make_goal(*, uid: str = PEER_UID, room_id: str = ROOM_ID) -> Goal:
    return Goal(
        goal_type=GoalType.ANSWER_PRIVATE_MESSAGE,
        payload={"sender_uid": uid, "room_id": room_id, "pending_reply_id": "reply-nav-1"},
    )


def make_state(
    screen: str,
    *,
    chat_shell_evidence: bool = False,
    verified: bool = False,
    uid: str = PEER_UID,
    room_id: str = ROOM_ID,
) -> WorldState:
    state = WorldState(adb_connected=True, ai_available=True)
    state.update_screen(screen, chat_shell_evidence=chat_shell_evidence, updated_at=10.0)
    if verified:
        state.set_current_conversation_verified(uid=uid, room_id=room_id, verified_at=10.0)
    return state


class NavigationGraphPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = [100.0]
        self.navigator = NavigationManager(
            now=lambda: self.clock[0],
            max_unknown_observations=3,
            max_recovery_attempts=3,
            max_same_state_attempts=2,
        )

    def plan(self, goal: Goal, state: WorldState, *, observed: bool = True):
        return self.navigator.plan_private_conversation(goal, state, observed_this_tick=observed)

    def test_01_open_chat_edge_accepts_multiple_real_results(self) -> None:
        results = NAVIGATION_GRAPH["open_chat"].expected_possible_results
        self.assertTrue({"ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION", "CHAT_OPEN_UNCLASSIFIED"} <= results)

    def test_02_world_map_plans_open_chat(self) -> None:
        plan = self.plan(make_goal(), make_state("WORLD_MAP"))
        self.assertEqual((plan.kind, plan.action), ("ACTION", "open_chat"))

    def test_03_hq_plans_open_chat(self) -> None:
        plan = self.plan(make_goal(), make_state("HQ_BASE"))
        self.assertEqual((plan.kind, plan.action), ("ACTION", "open_chat"))

    def test_04_alliance_chat_plans_private_tab(self) -> None:
        plan = self.plan(make_goal(), make_state("ALLIANCE_CHAT"))
        self.assertEqual((plan.kind, plan.action), ("ACTION", "open_private_chat"))

    def test_05_private_list_plans_verified_target_selection(self) -> None:
        goal = make_goal()
        state = make_state("PRIVATE_CHAT", chat_shell_evidence=True)
        first = self.plan(goal, state)
        self.assertEqual((first.kind, first.reason), ("WAIT", "private_chat_stability"))
        plan = self.plan(goal, state)
        self.assertEqual((plan.kind, plan.action), ("ACTION", "prepare_private_reply_target"))

    def test_06_already_at_verified_target_reaches_target_without_tap(self) -> None:
        plan = self.plan(make_goal(), make_state("PRIVATE_CONVERSATION", verified=True))
        self.assertEqual(plan.kind, "TARGET_REACHED")
        self.assertEqual(plan.task.status, NavigationStatus.TARGET_REACHED.value)

    def test_07_wrong_private_conversation_uses_observable_back_first(self) -> None:
        plan = self.plan(
            make_goal(),
            make_state("PRIVATE_CONVERSATION", verified=True, uid=OTHER_UID, room_id=OTHER_ROOM_ID),
        )
        self.assertEqual((plan.kind, plan.action), ("ACTION", "recover_back"))

    def test_08_chat_shell_unclassified_with_evidence_can_normalize(self) -> None:
        plan = self.plan(make_goal(), make_state("CHAT_OPEN_UNCLASSIFIED", chat_shell_evidence=True))
        self.assertEqual((plan.kind, plan.action), ("ACTION", "open_private_chat"))

    def test_09_chat_shell_unclassified_without_evidence_waits(self) -> None:
        plan = self.plan(make_goal(), make_state("CHAT_OPEN_UNCLASSIFIED"))
        self.assertEqual((plan.kind, plan.reason), ("WAIT", "chat_shell_not_verified"))

    def test_10_first_unknown_is_a_reobserve_not_a_tap(self) -> None:
        plan = self.plan(make_goal(), make_state("UNKNOWN"))
        self.assertEqual((plan.kind, plan.reason), ("WAIT", "transient_unknown_reobserve"))

    def test_11_third_unknown_with_prior_chat_evidence_attempts_controlled_recovery(self) -> None:
        goal = make_goal()
        state = make_state("PRIVATE_CHAT")
        state.update_screen("UNKNOWN", updated_at=11.0)
        self.plan(goal, state)
        self.plan(goal, state)
        plan = self.plan(goal, state)
        self.assertEqual((plan.kind, plan.action), ("ACTION", "recover_back"))
        self.assertEqual(plan.task.status, NavigationStatus.RECOVERING_TO_ANCHOR.value)

    def test_12_persistent_unknown_without_evidence_blocks_deterministically(self) -> None:
        goal = make_goal()
        state = make_state("UNKNOWN")
        self.plan(goal, state)
        self.plan(goal, state)
        plan = self.plan(goal, state)
        self.assertEqual((plan.kind, plan.reason), ("WAIT", "navigation_block_grace"))
        self.clock[0] += 1.0
        blocked = self.plan(goal, state)
        self.assertEqual(blocked.kind, "BLOCKED")
        self.assertEqual(blocked.reason, "unknown_without_safe_recovery_evidence")

    def test_13_recovery_budget_is_consumed_by_attempt_not_only_transport_failure(self) -> None:
        goal = make_goal()
        state = make_state("PRIVATE_CHAT")
        state.update_screen("UNKNOWN", updated_at=11.0)
        task = self.navigator.ensure_private_conversation_task(goal)
        for _ in range(3):
            self.plan(goal, state)
        plan = self.plan(goal, state)
        self.assertEqual(plan.action, "recover_back")
        self.navigator.action_started(goal, task, "recover_back", "UNKNOWN")
        self.assertEqual(task.recovery_count, 1)

    def test_30_persistent_unknown_with_chat_evidence_exhausts_recovery_budget(self) -> None:
        goal = make_goal()
        state = make_state("PRIVATE_CHAT")
        state.update_screen("UNKNOWN", updated_at=11.0)
        for _ in range(3):
            plan = self.plan(goal, state)
        self.assertEqual(plan.action, "recover_back")
        self.navigator.action_started(goal, plan.task, "recover_back", "UNKNOWN")
        self.navigator.action_result(goal, {"action": "recover_back"}, ok=True, result="executed")
        self.clock[0] += 3.0
        grace = self.plan(goal, state)
        self.assertEqual((grace.kind, grace.reason), ("WAIT", "navigation_block_grace"))
        self.clock[0] += 1.0
        blocked = self.plan(goal, state)
        self.assertEqual(blocked.kind, "BLOCKED")
        self.assertTrue(blocked.reason.startswith("same_state_action_exhausted:recover_back"))

    def test_14_recovery_to_world_anchor_replans_original_target(self) -> None:
        goal = make_goal()
        state = make_state("PRIVATE_CONVERSATION", verified=True, uid=OTHER_UID, room_id=OTHER_ROOM_ID)
        plan = self.plan(goal, state)
        self.navigator.action_started(goal, plan.task, "recover_back", state.screen)
        self.navigator.action_result(goal, {"action": "recover_back"}, ok=True, result="executed")
        state.update_screen("WORLD_MAP", updated_at=12.0)
        self.navigator.observation_after_action(goal, state)
        replan = self.plan(goal, state)
        self.assertEqual((replan.kind, replan.action), ("ACTION", "open_chat"))
        self.assertEqual(replan.task.target_uid, PEER_UID)

    def test_15_repeated_same_state_action_does_not_retry_forever(self) -> None:
        goal = make_goal()
        state = make_state("WORLD_MAP")
        plan = self.plan(goal, state)
        self.assertEqual(plan.action, "open_chat")
        self.navigator.action_started(goal, plan.task, "open_chat", state.screen)
        self.navigator.action_result(goal, {"action": "open_chat"}, ok=True, result="executed")
        self.clock[0] += 3.0
        grace = self.plan(goal, state)
        self.assertEqual((grace.kind, grace.reason), ("WAIT", "navigation_block_grace"))
        self.clock[0] += 1.0
        blocked = self.plan(goal, state)
        self.assertEqual(blocked.kind, "BLOCKED")
        self.assertIn("same_state_action_exhausted", blocked.reason)

    def test_16_incomplete_target_never_plans_a_physical_action(self) -> None:
        goal = make_goal(uid="", room_id="")
        plan = self.plan(goal, make_state("PRIVATE_CHAT"))
        self.assertEqual((plan.kind, plan.reason), ("BLOCKED", "navigation_target_incomplete"))

    def test_17_navigation_target_survives_intermediate_screens(self) -> None:
        goal = make_goal()
        state = make_state("WORLD_MAP")
        initial = self.plan(goal, state).task.navigation_id
        for screen in ("UNKNOWN", "ALLIANCE_CHAT", "PRIVATE_CHAT"):
            state.update_screen(screen, chat_shell_evidence=(screen == "CHAT_OPEN_UNCLASSIFIED"))
            self.plan(goal, state)
        task = self.navigator.ensure_private_conversation_task(goal)
        self.assertEqual(task.navigation_id, initial)
        self.assertEqual((task.target_uid, task.target_room_id), (PEER_UID, ROOM_ID))

    def test_18_manual_screen_change_replans_from_reality(self) -> None:
        goal = make_goal()
        state = make_state("WORLD_MAP")
        self.plan(goal, state)
        # The human opens the private list before PEva can complete its old route.
        state.update_screen("PRIVATE_CHAT", chat_shell_evidence=True, updated_at=12.0)
        first = self.plan(goal, state)
        self.assertEqual((first.kind, first.reason), ("WAIT", "private_chat_stability"))
        plan = self.plan(goal, state)
        self.assertEqual((plan.kind, plan.action), ("ACTION", "prepare_private_reply_target"))

    def test_19_task_is_recreated_only_when_its_target_changes(self) -> None:
        goal = make_goal()
        original = self.navigator.ensure_private_conversation_task(goal)
        goal.payload["sender_uid"] = OTHER_UID
        goal.payload["room_id"] = OTHER_ROOM_ID
        replacement = self.navigator.ensure_private_conversation_task(goal)
        self.assertNotEqual(original.navigation_id, replacement.navigation_id)
        self.assertEqual(replacement.target_uid, OTHER_UID)

    def test_20_observation_after_action_keeps_waiting_until_a_changed_screen(self) -> None:
        goal = make_goal()
        state = make_state("WORLD_MAP")
        plan = self.plan(goal, state)
        self.navigator.action_started(goal, plan.task, "open_chat", state.screen)
        task = self.navigator.action_result(goal, {"action": "open_chat"}, ok=True, result="executed")
        self.assertTrue(task.awaiting_observation)
        task = self.navigator.observation_after_action(goal, state)
        self.assertTrue(task.awaiting_observation)
        state.update_screen("ALLIANCE_CHAT", updated_at=12.0)
        replan = self.plan(goal, state)
        self.assertFalse(replan.task.awaiting_observation)
        self.assertEqual((replan.kind, replan.action), ("ACTION", "open_private_chat"))


class ScreenObservationTests(unittest.TestCase):
    def test_21_chat_shell_transition_is_not_collapsed_to_generic_unknown(self) -> None:
        result = classify_screen({"private_selected": 0.84, "alliance_unselected": 0.30})
        self.assertEqual(result["screen"], "CHAT_OPEN_UNCLASSIFIED")
        self.assertTrue(result["chat_shell_evidence"])

    def test_22_unknown_without_shell_evidence_stays_fail_closed(self) -> None:
        result = classify_screen({"private_selected": 0.2, "alliance_unselected": 0.1})
        self.assertEqual(result["screen"], "UNKNOWN")
        self.assertFalse(result["chat_shell_evidence"])

    def test_23_world_state_clears_binding_when_the_screen_leaves_conversation(self) -> None:
        state = make_state("PRIVATE_CONVERSATION", verified=True)
        state.update_screen("PRIVATE_CHAT")
        self.assertFalse(state.current_conversation_verified)
        self.assertIsNone(state.current_conversation_uid)


class ActionPreconditionTests(unittest.TestCase):
    def test_24_generic_unknown_never_presses_recovery_back(self) -> None:
        executor = ActionExecutor(logger=lambda _line: None)
        with patch("archive.legacy_runtime.action_ex.adb_controll.back", return_value=True) as back:
            result = executor.execute({"action": "recover_back"}, {"screen": "UNKNOWN"})
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unsafe_recovery_back")
        back.assert_not_called()

    def test_25_evidenced_unknown_can_use_one_controlled_back(self) -> None:
        executor = ActionExecutor(logger=lambda _line: None)
        with patch("archive.legacy_runtime.action_ex.adb_controll.back", return_value=True) as back:
            result = executor.execute(
                {"action": "recover_back"},
                {"screen": "UNKNOWN", "last_known_screen": "PRIVATE_CHAT"},
            )
        self.assertTrue(result.ok)
        back.assert_called_once_with()

    def test_26_modelled_deviations_are_safe_or_deterministically_terminal(self) -> None:
        # A small deterministic property test: 25 arbitrary detector paths
        # must only yield graph actions, waits, target success, or one named
        # terminal block.  It intentionally includes manual-looking jumps.
        rng = random.Random(20261019)
        screens = [
            "WORLD_MAP", "HQ_BASE", "ALLIANCE_CHAT", "PRIVATE_CHAT",
            "PRIVATE_CONVERSATION", "CHAT_OPEN_UNCLASSIFIED", "UNKNOWN",
        ]
        for _ in range(25):
            navigator = NavigationManager(
                now=lambda: 100.0,
                max_unknown_observations=2,
                max_recovery_attempts=2,
                max_same_state_attempts=1,
            )
            goal = make_goal()
            state = make_state("WORLD_MAP")
            for screen in [rng.choice(screens) for _ in range(12)]:
                state.update_screen(
                    screen,
                    chat_shell_evidence=(screen == "CHAT_OPEN_UNCLASSIFIED"),
                )
                plan = navigator.plan_private_conversation(goal, state, observed_this_tick=True)
                self.assertIn(plan.kind, {"ACTION", "WAIT", "TARGET_REACHED", "BLOCKED"})
                if plan.kind == "ACTION":
                    self.assertIn(plan.action, NAVIGATION_GRAPH)
                    navigator.action_started(goal, plan.task, str(plan.action), state.screen)
                    navigator.action_result(goal, {"action": plan.action}, ok=True, result="executed")
                    navigator.observation_after_action(goal, state)
                if plan.kind in {"TARGET_REACHED", "BLOCKED"}:
                    break


class _Capture:
    def __init__(self, messages: list[ChatMessage]) -> None:
        self.messages = list(messages)

    def get_message(self, timeout: float = 0) -> ChatMessage | None:
        del timeout
        return self.messages.pop(0) if self.messages else None


class _Pending:
    def __init__(self, message: ChatMessage) -> None:
        self.reply_id = "reply-nav-1"
        self.player_uid = message.sender_uid
        self.room_id = message.room_id
        self.text = "I am good, thanks."


class _Agent:
    def __init__(self) -> None:
        self.pending: dict[str, _Pending] = {}
        self.sent: list[str] = []

    def process_chat_message(self, message: ChatMessage, **_kwargs: object) -> SimpleNamespace:
        pending = _Pending(message)
        self.pending[pending.reply_id] = pending
        return SimpleNamespace(status="reply_ready", reply_ready=True, pending_reply=pending)

    def get_pending_reply(self, reply_id: str) -> _Pending | None:
        return self.pending.get(reply_id)

    def mark_reply_sent(self, reply_id: str) -> None:
        self.sent.append(reply_id)
        self.pending.pop(reply_id, None)


class _Executor:
    def __init__(self) -> None:
        self.actions: list[dict[str, object]] = []

    def execute(self, action: dict[str, object], _state: dict[str, object]) -> SimpleNamespace:
        self.actions.append(dict(action))
        if action["action"] == "prepare_private_reply_target":
            return SimpleNamespace(
                ok=True,
                status="target_verified",
                data={
                    "delivery_state": "NOT_ATTEMPTED",
                    "navigation_verified": True,
                    "target_uid_verified": True,
                    "room_verified": True,
                    "current_conversation_uid": PEER_UID,
                    "current_conversation_room_id": ROOM_ID,
                    "reason": "target_verified",
                },
            )
        if action["action"] == "reply_private":
            return SimpleNamespace(
                ok=True,
                status="sent",
                data={
                    "delivery_state": "CONFIRMED",
                    "navigation_verified": True,
                    "target_uid_verified": True,
                    "room_verified": True,
                    "send_tap_attempted": True,
                    "server_echo_seen": True,
                    "server_echo_seq": 812,
                    "reason": "server_echo_confirmed",
                },
            )
        return SimpleNamespace(ok=True, status="executed", data={})


def incoming() -> ChatMessage:
    return ChatMessage(
        room_id=ROOM_ID,
        group="custom",
        sender_uid=PEER_UID,
        sender_name="Alex",
        text="can you help?",
        seq_id=91,
    )


class RealTraceRegressionTests(unittest.TestCase):
    def build_loop(self, screens: list[str]):
        clock = [100.0]
        lines: list[str] = []
        executor = _Executor()
        agent = _Agent()

        def provider() -> dict[str, object]:
            return {"screen": screens.pop(0) if screens else "PRIVATE_CONVERSATION", "unread": {}}

        loop = PlayerLoop(
            capture=_Capture([incoming()]),
            agent=agent,
            executor=executor,
            screen_provider=provider,
            config=PlayerLoopConfig(
                auto_send=True,
                private_batch_window=0,
                screen_poll_interval=0.1,
                recovery_retry_delay=0.1,
                enable_terminal_decision_control=False,
                humanization_enabled=False,
                agency_enabled=False,
            ),
            logger=lines.append,
            now=lambda: clock[0],
        )
        loop.set_runtime_availability(adb_connected=True, ai_available=True)
        return loop, executor, agent, clock, lines

    def test_27_real_world_unknown_wrong_conversation_trace_recovers_without_manual_click(self) -> None:
        # This is the reported WORLD -> UNKNOWN -> wrong PC -> UNKNOWN ->
        # wrong PC path.  Each stale/unknown observation must be allowed to
        # settle before the next physical action is considered.
        loop, executor, agent, clock, lines = self.build_loop([
            "WORLD_MAP", "WORLD_MAP", "UNKNOWN", "PRIVATE_CONVERSATION", "UNKNOWN",
            "PRIVATE_CONVERSATION", "PRIVATE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION",
            "PRIVATE_CONVERSATION",
        ])
        for _ in range(9):
            loop.run_once()
            clock[0] += 1.0

        actions = [str(action["action"]) for action in executor.actions]
        self.assertEqual(
            actions,
            ["open_chat", "recover_back", "prepare_private_reply_target", "reply_private"],
        )
        self.assertEqual(agent.sent, ["reply-nav-1"])
        self.assertFalse(any("goal=- reason=No pending goal" in line for line in lines))
        self.assertTrue(any(line.startswith("[NAV TARGET REACHED]") for line in lines))
        self.assertTrue(any(line.startswith("[UI EFFECT WAIT]") for line in lines))

    def test_28_no_send_before_the_verified_target_step(self) -> None:
        loop, executor, _agent, clock, _lines = self.build_loop(
            ["WORLD_MAP", "WORLD_MAP", "ALLIANCE_CHAT", "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CHAT"]
        )
        for _ in range(6):
            loop.run_once()
            clock[0] += 1.0
        actions = [str(action["action"]) for action in executor.actions]
        self.assertEqual(actions, ["open_chat", "open_private_chat", "prepare_private_reply_target"])
        self.assertNotIn("reply_private", actions)

    def test_29_parent_goal_remains_traceable_while_waiting_for_observation(self) -> None:
        loop, _executor, _agent, clock, _lines = self.build_loop(["WORLD_MAP", "UNKNOWN"])
        loop.run_once()
        clock[0] += 1.0
        loop.run_once()
        waiting = loop.goal_manager.get_waiting_goal()
        self.assertIsNotNone(waiting)
        self.assertEqual(waiting.goal_type, GoalType.ANSWER_PRIVATE_MESSAGE)
        self.assertIn("navigation_task", waiting.payload)
        self.assertIn(waiting.status, {GoalStatus.PENDING, GoalStatus.ACTIVE})


if __name__ == "__main__":
    unittest.main()
