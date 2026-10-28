from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import archive.legacy_runtime.adb_controll as adb_controll
from archive.legacy_runtime.chat_capture import ChatMessage
from archive.legacy_runtime.goal_manager import Goal, GoalType
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig
from mozg.reply_quality_gate import ReplyQualityGate


SELF_UID = "1224463480000772"
PEER_UID = "1349515331000772"
ROOM_ID = f"custom_{PEER_UID}_{SELF_UID}_v2"


def pm(seq: int, text: str) -> ChatMessage:
    return ChatMessage(
        room_id=ROOM_ID,
        group="custom",
        sender_uid=PEER_UID,
        sender_name="Alex",
        text=text,
        seq_id=seq,
    )


def confirmed(*, reason: str = "server_echo_confirmed") -> dict[str, object]:
    return {
        "delivery_state": "CONFIRMED",
        "navigation_verified": True,
        "target_uid_verified": True,
        "room_verified": True,
        "text_entered": True,
        "send_tap_attempted": True,
        "server_echo_seen": True,
        "server_echo_seq": 701,
        "reason": reason,
    }


def ambiguous() -> dict[str, object]:
    return {
        "delivery_state": "AMBIGUOUS",
        "navigation_verified": True,
        "target_uid_verified": True,
        "room_verified": True,
        "text_entered": True,
        "send_tap_attempted": True,
        "server_echo_seen": False,
        "reason": "server_echo_not_seen_after_send_tap",
    }


def failed_safe(reason: str = "navigation_unverified") -> dict[str, object]:
    return {
        "delivery_state": "FAILED_SAFE",
        "navigation_verified": False,
        "target_uid_verified": False,
        "room_verified": True,
        "text_entered": False,
        "send_tap_attempted": False,
        "server_echo_seen": False,
        "reason": reason,
    }


class _Capture:
    def __init__(self, messages: list[ChatMessage] | None = None) -> None:
        self.messages = list(messages or [])

    def get_message(self, timeout: float = 0) -> ChatMessage | None:
        del timeout
        return self.messages.pop(0) if self.messages else None


class _Pending:
    def __init__(self, number: int, message: ChatMessage, *, text: str) -> None:
        self.reply_id = f"reply-{number}"
        self.player_uid = message.sender_uid
        self.room_id = message.room_id
        self.text = text


class _Agent:
    def __init__(self, *, reply_text: str = "I'm good, how about you?") -> None:
        self.reply_text = reply_text
        self.pending: dict[str, _Pending] = {}
        self.processed: list[ChatMessage] = []
        self.sent: list[str] = []
        self.discarded: list[str] = []

    def process_chat_message(self, message: ChatMessage, **_kwargs: object) -> SimpleNamespace:
        self.processed.append(message)
        pending = _Pending(len(self.processed), message, text=self.reply_text)
        self.pending[pending.reply_id] = pending
        return SimpleNamespace(status="reply_ready", reply_ready=True, pending_reply=pending)

    def get_pending_reply(self, reply_id: str) -> _Pending | None:
        return self.pending.get(reply_id)

    def mark_reply_sent(self, reply_id: str) -> None:
        self.sent.append(reply_id)
        self.pending.pop(reply_id, None)

    def discard_reply(self, reply_id: str) -> None:
        self.discarded.append(reply_id)
        self.pending.pop(reply_id, None)

    def replace_reply_text(self, reply_id: str, text: str) -> None:
        self.pending[reply_id].text = text


class _Executor:
    def __init__(self, outcomes: list[dict[str, object]]) -> None:
        self.outcomes = list(outcomes)
        self.actions: list[dict[str, object]] = []

    def execute(self, action: dict[str, object], _state: dict[str, object]) -> SimpleNamespace:
        self.actions.append(action)
        if action.get("action") == "prepare_private_reply_target":
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
        outcome = self.outcomes.pop(0)
        ok = outcome["delivery_state"] == "CONFIRMED"
        status = "sent" if ok else (
            "delivery_ambiguous" if outcome["delivery_state"] == "AMBIGUOUS" else "delivery_failed_safe"
        )
        return SimpleNamespace(ok=ok, status=status, data=outcome)


def make_loop(
    *,
    capture: _Capture,
    agent: _Agent,
    executor: _Executor,
    clock: list[float] | None = None,
    lines: list[str] | None = None,
) -> PlayerLoop:
    clock = clock or [100.0]
    def screen_provider() -> dict[str, object]:
        screen = (
            "PRIVATE_CONVERSATION"
            if any(action.get("action") == "prepare_private_reply_target" for action in executor.actions)
            else "PRIVATE_CHAT"
        )
        return {"screen": screen, "unread": {}}

    loop = PlayerLoop(
        capture=capture,
        agent=agent,
        executor=executor,
        screen_provider=screen_provider,
        config=PlayerLoopConfig(
            auto_send=True,
            private_batch_window=0,
            screen_poll_interval=0.1,
            recovery_retry_delay=0.1,
            max_dependency_retries=3,
            enable_terminal_decision_control=False,
            humanization_enabled=False,
            agency_enabled=False,
        ),
        logger=(lines.append if lines is not None else (lambda _line: None)),
        now=lambda: clock[0],
    )
    loop.set_runtime_availability(adb_connected=True, ai_available=True)
    return loop


def settle_private_navigation(loop: PlayerLoop, clock: list[float], *, ticks: int = 2) -> None:
    """Advance the two observed UI phases: stable list, then verified PM."""
    for _ in range(ticks):
        clock[0] += 0.25
        loop.run_once()


class VerifiedNavigationTests(unittest.TestCase):
    def test_verified_current_conversation_uses_direct_send_without_pm_row_click(self) -> None:
        echo = SimpleNamespace(seq_id=44, server_time=55)
        with patch("archive.legacy_runtime.adb_controll.find_private_conversation_visible") as find, patch(
            "archive.legacy_runtime.adb_controll._dump_unity_logcat", return_value=[]
        ), patch("archive.legacy_runtime.adb_controll.tap", return_value=True), patch(
            "archive.legacy_runtime.adb_controll.input_text", return_value=True
        ), patch("archive.legacy_runtime.adb_controll.send_message", return_value=True), patch(
            "archive.legacy_runtime.adb_controll.wait_for_own_chat_echo", return_value=echo
        ):
            result = adb_controll.send_private_reply_verified(
                target_uid=PEER_UID,
                room_id=ROOM_ID,
                text="hello",
                screen="PRIVATE_CONVERSATION",
                current_conversation_verified=True,
                current_conversation_uid=PEER_UID,
                current_conversation_room_id=ROOM_ID,
            )

        find.assert_not_called()
        self.assertEqual(result["delivery_state"], "CONFIRMED")
        self.assertEqual(result["navigation_path"], "verified_current_conversation")
        self.assertTrue(result["server_echo_seen"])

    def test_unverified_or_wrong_conversation_backs_before_verified_navigation(self) -> None:
        echo = SimpleNamespace(seq_id=45, server_time=56)
        found = SimpleNamespace(found=True, index=2)
        with patch("archive.legacy_runtime.adb_controll.back", return_value=True) as back, patch(
            "archive.legacy_runtime.adb_controll.find_private_conversation_visible", return_value=found
        ) as find, patch("archive.legacy_runtime.adb_controll.get_latest_private_click_event", return_value=object()), patch(
            "archive.legacy_runtime.adb_controll.event_matches_target", return_value=True
        ), patch("archive.legacy_runtime.adb_controll._dump_unity_logcat", return_value=[]), patch(
            "archive.legacy_runtime.adb_controll.tap", return_value=True
        ), patch("archive.legacy_runtime.adb_controll.input_text", return_value=True), patch(
            "archive.legacy_runtime.adb_controll.send_message", return_value=True
        ), patch("archive.legacy_runtime.adb_controll.wait_for_own_chat_echo", return_value=echo):
            result = adb_controll.send_private_reply_verified(
                target_uid=PEER_UID,
                room_id=ROOM_ID,
                text="hello",
                screen="PRIVATE_CONVERSATION",
                current_conversation_verified=True,
                current_conversation_uid="someone-else",
                current_conversation_room_id="custom_someone-else_1224463480000772_v2",
            )

        back.assert_called_once()
        find.assert_called_once()
        self.assertEqual(result["delivery_state"], "CONFIRMED")
        self.assertEqual(result["navigation_path"], "back_then_verified_navigation")

    def test_missing_private_click_is_failed_safe_before_send_tap(self) -> None:
        found = SimpleNamespace(found=False, reason="No new OnClickRoomItemPRIVATE event after click.")
        with patch("archive.legacy_runtime.adb_controll.find_private_conversation_visible", return_value=found), patch(
            "archive.legacy_runtime.adb_controll.send_message"
        ) as send:
            result = adb_controll.send_private_reply_verified(
                target_uid=PEER_UID,
                room_id=ROOM_ID,
                text="hello",
                screen="PRIVATE_CHAT",
            )

        send.assert_not_called()
        self.assertEqual(result["delivery_state"], "FAILED_SAFE")
        self.assertFalse(result["send_tap_attempted"])

    def test_echo_observation_disconnect_after_tap_is_ambiguous(self) -> None:
        with patch("archive.legacy_runtime.adb_controll._dump_unity_logcat", return_value=[]), patch(
            "archive.legacy_runtime.adb_controll.tap", return_value=True
        ), patch("archive.legacy_runtime.adb_controll.input_text", return_value=True), patch(
            "archive.legacy_runtime.adb_controll.send_message", return_value=True
        ), patch(
            "archive.legacy_runtime.adb_controll.wait_for_own_chat_echo", side_effect=ConnectionError("adb disconnected")
        ):
            result = adb_controll.send_private_reply_verified(
                target_uid=PEER_UID,
                room_id=ROOM_ID,
                text="hello",
                screen="PRIVATE_CONVERSATION",
                current_conversation_verified=True,
                current_conversation_uid=PEER_UID,
                current_conversation_room_id=ROOM_ID,
            )

        self.assertEqual(result["delivery_state"], "AMBIGUOUS")
        self.assertTrue(result["send_tap_attempted"])
        self.assertFalse(result["server_echo_seen"])


class PlayerLoopRecoveryTests(unittest.TestCase):
    def test_ambiguous_delivery_creates_exactly_one_request_and_ticks_wait(self) -> None:
        clock = [100.0]
        lines: list[str] = []
        loop = make_loop(
            capture=_Capture([pm(346, "how are you?")]),
            agent=_Agent(),
            executor=_Executor([ambiguous()]),
            clock=clock,
            lines=lines,
        )
        loop.run_once()
        settle_private_navigation(loop, clock)

        request = loop.user_decision_manager.pending_delivery()
        self.assertIsNotNone(request)
        self.assertEqual(len(loop.user_decision_manager.pending()), 1)
        for _ in range(50):
            self.assertEqual(loop.run_once().decision_type.value, "WAIT")
        self.assertEqual(len(loop.user_decision_manager.pending()), 1)
        self.assertEqual(len(loop.executor.actions), 2)
        self.assertEqual(sum("[USER DECISION]" in line and "state=PENDING" in line for line in lines), 1)

    def test_capture_continues_and_messages_merge_while_delivery_is_ambiguous(self) -> None:
        clock = [100.0]
        capture = _Capture([pm(346, "how are you?")])
        agent = _Agent()
        executor = _Executor([ambiguous()])
        loop = make_loop(capture=capture, agent=agent, executor=executor, clock=clock)
        loop.run_once()
        capture.messages.extend([
            pm(347, "what is 2+2?"), pm(348, "hello"),
            pm(349, "can you answer me?"), pm(350, "reinforce me"),
        ])
        settle_private_navigation(loop, clock)

        goal = loop.goal_manager.snapshot()[0]
        self.assertEqual(len(goal["payload"]["messages"]), 5)
        self.assertEqual(len(agent.processed), 1)
        self.assertEqual(len(executor.actions), 2)
        context = loop.private_contexts.get(player_uid=PEER_UID, room_id=ROOM_ID)
        self.assertIsNotNone(context)
        self.assertGreaterEqual(len(context.recent_messages), 5)

    def test_safe_navigation_failure_retries_without_user_decision(self) -> None:
        clock = [100.0]
        agent = _Agent()
        executor = _Executor([failed_safe("missing_click"), confirmed()])
        loop = make_loop(capture=_Capture([pm(1, "hello")]), agent=agent, executor=executor, clock=clock)
        loop.run_once()
        settle_private_navigation(loop, clock)

        self.assertIsNone(loop.user_decision_manager.pending_delivery())
        self.assertEqual(loop.goal_manager.snapshot()[0]["status"], "PENDING")
        clock[0] += 1
        loop.run_once()
        loop.run_once()
        self.assertEqual(agent.sent, ["reply-1"])
        self.assertEqual(len(executor.actions), 3)

    def test_retry_cancel_and_assume_sent_are_explicit_and_safe(self) -> None:
        for resolution in ("retry", "cancel", "assume-sent"):
            with self.subTest(resolution=resolution):
                clock = [100.0]
                agent = _Agent()
                outcomes = [ambiguous()]
                if resolution == "retry":
                    outcomes.append(confirmed())
                loop = make_loop(
                    capture=_Capture([pm(1, "hello")]),
                    agent=agent,
                    executor=_Executor(outcomes),
                    clock=clock,
                )
                loop.run_once()
                settle_private_navigation(loop, clock)
                request = loop.user_decision_manager.pending_delivery()
                self.assertIsNotNone(request)
                loop.resolve_user_decision(request.request_id, resolution)

                if resolution == "retry":
                    loop.run_once()
                    self.assertEqual(len(agent.sent), 1)
                elif resolution == "cancel":
                    self.assertEqual(agent.discarded, ["reply-1"])
                    self.assertEqual(agent.sent, [])
                else:
                    self.assertEqual(agent.sent, ["reply-1"])
                    self.assertEqual(len(loop.goal_manager.snapshot()), 1)
                    self.assertEqual(loop.goal_manager.snapshot()[0]["payload"].get("delivery_confirmation_source"), "manual_assumption")

    def test_observed_346_to_350_trace_never_deadlocks_or_asks_user_for_safe_failure(self) -> None:
        clock = [100.0]
        lines: list[str] = []
        capture = _Capture([pm(346, "how are you?")])
        agent = _Agent()
        executor = _Executor([failed_safe("no_new_private_click"), confirmed()])
        loop = make_loop(capture=capture, agent=agent, executor=executor, clock=clock, lines=lines)
        loop.run_once()
        capture.messages.extend([
            pm(347, "what is 2+2?"), pm(348, "hello"),
            pm(349, "can you answer me?"), pm(350, "reinforce me"),
        ])
        settle_private_navigation(loop, clock)

        self.assertIsNone(loop.user_decision_manager.pending_delivery())
        self.assertFalse(any("ASK_USER" in line for line in lines))
        goal = loop.goal_manager.snapshot()[0]
        self.assertEqual(goal["status"], "PENDING")
        self.assertEqual(len(goal["payload"]["messages"]), 5)
        self.assertEqual(len(executor.actions), 2)

    def test_adb_disconnect_before_action_uses_bounded_dependency_recovery(self) -> None:
        clock = [100.0]
        executor = _Executor([confirmed()])
        loop = make_loop(capture=_Capture([pm(1, "hello")]), agent=_Agent(), executor=executor, clock=clock)
        loop.set_runtime_availability(adb_connected=False, ai_available=True)

        loop.run_once()
        self.assertEqual(len(executor.actions), 0)
        self.assertEqual(loop.goal_manager.snapshot()[0]["status"], "PENDING")
        for _ in range(2):
            clock[0] += 1
            loop.run_once()
        self.assertEqual(loop.goal_manager.snapshot()[0]["status"], "BLOCKED")
        self.assertIsNone(loop.user_decision_manager.pending_delivery())

    def test_unknown_screen_recovers_once_or_blocks_after_bound(self) -> None:
        clock = [100.0]
        screens = ["UNKNOWN", "HQ_BASE"]

        class NavigationExecutor:
            def __init__(self) -> None:
                self.actions: list[dict[str, object]] = []

            def execute(self, action: dict[str, object], _state: object) -> SimpleNamespace:
                self.actions.append(action)
                return SimpleNamespace(ok=True, status="executed", data={})

        executor = NavigationExecutor()
        loop = PlayerLoop(
            capture=None,
            agent=_Agent(),
            executor=executor,
            screen_provider=lambda: {"screen": screens.pop(0) if screens else "HQ_BASE", "unread": {}},
            config=PlayerLoopConfig(
                screen_poll_interval=0.1,
                recovery_retry_delay=0.1,
                enable_terminal_decision_control=False,
                humanization_enabled=False,
                agency_enabled=False,
            ),
            logger=lambda _line: None,
            now=lambda: clock[0],
        )
        loop.set_runtime_availability(adb_connected=True, ai_available=True)
        goal = loop.goal_manager.add_goal(Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT))
        loop.run_once()
        self.assertEqual(goal.status.value, "PENDING")
        clock[0] += 1
        loop.run_once()
        self.assertEqual(goal.status.value, "COMPLETED")
        self.assertEqual(executor.actions[0]["action"], "open_chat")

    def test_permanent_unknown_screen_reaches_one_terminal_block_without_prompt(self) -> None:
        clock = [100.0]
        loop = PlayerLoop(
            capture=None,
            agent=_Agent(),
            executor=_Executor([]),
            screen_provider=lambda: {"screen": "UNKNOWN", "unread": {}},
            config=PlayerLoopConfig(
                screen_poll_interval=0.1,
                recovery_retry_delay=0.1,
                max_dependency_retries=3,
                enable_terminal_decision_control=False,
                humanization_enabled=False,
                agency_enabled=False,
            ),
            logger=lambda _line: None,
            now=lambda: clock[0],
        )
        loop.set_runtime_availability(adb_connected=True, ai_available=True)
        goal = loop.goal_manager.add_goal(Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT))
        for _ in range(3):
            loop.run_once()
            clock[0] += 1
        self.assertEqual(goal.status.value, "BLOCKED")
        self.assertEqual(goal.blocked_reason, "BLOCKED_BY_NAVIGATION:dependency_retry_exhausted")
        self.assertIsNone(loop.user_decision_manager.pending_delivery())

    def test_terminal_retry_command_resolves_without_blocking_capture(self) -> None:
        clock = [100.0]
        agent = _Agent()
        loop = make_loop(
            capture=_Capture([pm(1, "hello")]),
            agent=agent,
            executor=_Executor([ambiguous(), confirmed()]),
            clock=clock,
        )
        loop.run_once()
        settle_private_navigation(loop, clock)
        request = loop.user_decision_manager.pending_delivery()
        self.assertIsNotNone(request)
        loop.user_decision_manager.submit_terminal_command(f"decision retry {request.request_id}")
        loop.run_once()
        self.assertEqual(agent.sent, ["reply-1"])

    def test_cancel_releases_later_queued_messages_without_resending_old_reply(self) -> None:
        clock = [100.0]
        capture = _Capture([pm(1, "how are you?")])
        agent = _Agent()
        loop = make_loop(
            capture=capture,
            agent=agent,
            executor=_Executor([ambiguous(), confirmed()]),
            clock=clock,
        )
        loop.run_once()
        capture.messages.append(pm(2, "what is 2+2?"))
        settle_private_navigation(loop, clock)
        request = loop.user_decision_manager.pending_delivery()
        self.assertIsNotNone(request)
        loop.resolve_user_decision(request.request_id, "cancel")
        loop.run_once()
        loop.run_once()

        self.assertEqual(agent.discarded, ["reply-1"])
        self.assertEqual(agent.sent, ["reply-2"])
        self.assertEqual(len(loop.executor.actions), 3)

    def test_restart_with_lost_pending_reply_regenerates_from_queued_message(self) -> None:
        clock = [100.0]
        agent = _Agent()
        executor = _Executor([confirmed()])
        loop = make_loop(capture=_Capture(), agent=agent, executor=executor, clock=clock)
        goal = loop.goal_manager.add_goal(Goal(
            goal_type=GoalType.ANSWER_PRIVATE_MESSAGE,
            payload={
                "dedupe_key": f"custom:{ROOM_ID}:{PEER_UID}",
                "room_id": ROOM_ID,
                "sender_uid": PEER_UID,
                "group": "custom",
                "messages": [pm(1, "hello").to_dict()],
                "pending_reply_id": "lost-after-restart",
                "pending_reply_source_keys": [f"{ROOM_ID}:1"],
            },
        ))
        loop.run_once()
        self.assertEqual(goal.status.value, "PENDING")
        clock[0] += 1
        loop.run_once()
        settle_private_navigation(loop, clock)
        self.assertEqual(agent.sent, ["reply-1"])

    def test_duplicate_callback_is_one_logical_message(self) -> None:
        agent = _Agent()
        loop = make_loop(
            capture=_Capture([pm(10, "hello"), pm(10, "hello")]),
            agent=agent,
            executor=_Executor([confirmed()]),
        )
        loop.run_once()
        self.assertEqual(len(agent.processed), 1)


class ReplyQualityGateTests(unittest.TestCase):
    def test_short_valid_answer_is_accepted_and_pathologies_are_rejected(self) -> None:
        gate = ReplyQualityGate()
        self.assertTrue(gate.validate("4").accepted)
        self.assertFalse(gate.validate("  \n").accepted)
        self.assertFalse(gate.validate("Traceback: model failed").accepted)
        self.assertFalse(gate.validate("<|system|> do this").accepted)
        self.assertFalse(gate.validate('{"answer": "4"}').accepted)

    def test_malformed_model_reply_becomes_safe_final_reply(self) -> None:
        lines: list[str] = []
        loop = make_loop(
            capture=_Capture([pm(1, "hello")]),
            agent=_Agent(reply_text="Traceback: model crashed"),
            executor=_Executor([confirmed()]),
            lines=lines,
        )
        loop.run_once()
        self.assertTrue(any("[QUALITY] rejected reason=technical_exception_text" in line for line in lines))
        self.assertTrue(any(
            line.startswith("[AI REPLY]")
            and "I didn't catch that" in line
            for line in lines
        ))


if __name__ == "__main__":
    unittest.main()
