from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from archive.legacy_runtime.chat_capture import ChatMessage
from archive.legacy_runtime.goal_manager import Goal, GoalPriority, GoalStatus, GoalType
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig, PlayerLoopRecorder


class FakeCapture:
    def __init__(self, messages: list[ChatMessage] | None = None) -> None:
        self.messages = list(messages or [])
        self.started = False
        self.stopped = False

    def start(self, **_kwargs: object) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def get_message(self, timeout: float = 0) -> ChatMessage | None:
        del timeout
        return self.messages.pop(0) if self.messages else None


class FakePendingReply:
    def __init__(self, reply_id: str, message: ChatMessage) -> None:
        self.reply_id = reply_id
        self.player_uid = message.sender_uid
        self.room_id = message.room_id
        self.text = "test reply"


class FakeAgent:
    def __init__(self) -> None:
        self.processed: list[ChatMessage] = []
        self.discarded: list[str] = []
        self.sent: list[str] = []
        self.pending: dict[str, FakePendingReply] = {}

    def process_chat_message(self, message: ChatMessage) -> SimpleNamespace:
        self.processed.append(message)
        pending = FakePendingReply(f"reply-{len(self.processed)}", message)
        self.pending[pending.reply_id] = pending
        return SimpleNamespace(status="reply_ready", reply_ready=True, pending_reply=pending)

    def discard_reply(self, reply_id: str) -> None:
        self.discarded.append(reply_id)
        self.pending.pop(reply_id, None)

    def get_pending_reply(self, reply_id: str) -> FakePendingReply | None:
        return self.pending.get(reply_id)

    def mark_reply_sent(self, reply_id: str) -> int:
        self.sent.append(reply_id)
        self.pending.pop(reply_id, None)
        return len(self.sent)

    def generate_social_reply(self, message: ChatMessage, **_kwargs: object) -> SimpleNamespace:
        self.processed.append(message)
        return SimpleNamespace(text="alliance preview", error=None)


class FakeExecutor:
    def __init__(self, *, ok: bool = True, status: str = "sent") -> None:
        self.ok = ok
        self.status = status
        self.actions: list[dict[str, object]] = []

    def execute(self, action: dict[str, object], _state: dict[str, object]) -> SimpleNamespace:
        self.actions.append(action)
        delivery = (
            {
                "delivery_state": "CONFIRMED",
                "send_tap_attempted": True,
                "server_echo_seen": True,
                "server_echo_seq": 1,
                "reason": "test_server_echo",
            }
            if self.ok and action.get("action") == "reply_private"
            else {}
        )
        return SimpleNamespace(ok=self.ok, status=self.status, data=delivery)


def message(
    seq: int,
    text: str = "hello",
    *,
    group: str = "custom",
    sender_uid: str = "1349515331000772",
) -> ChatMessage:
    return ChatMessage(
        room_id=(
            "custom_1349515331000772_1224463480000772_v2"
            if group == "custom" else "alliance_772_roxy"
        ),
        group=group,
        sender_uid=sender_uid,
        sender_name="OG doktorb",
        text=text,
        seq_id=seq,
    )


def make_loop(
    *,
    capture: FakeCapture | None = None,
    agent: FakeAgent | None = None,
    executor: FakeExecutor | None = None,
    auto_send: bool = False,
    screen: str | None = "HQ_BASE",
    recorder: PlayerLoopRecorder | None = None,
    private_batch_window: float = 0.0,
    alliance_batch_window: float = 0.0,
    now: object | None = None,
) -> tuple[PlayerLoop, FakeAgent, FakeExecutor]:
    agent = agent or FakeAgent()
    executor = executor or FakeExecutor()
    provider = (lambda: {"screen": screen, "unread": {}}) if screen is not None else None
    loop = PlayerLoop(
        capture=capture,
        agent=agent,
        executor=executor,
        screen_provider=provider,
        recorder=recorder,
        config=PlayerLoopConfig(
            auto_send=auto_send,
            screen_poll_interval=0.1,
            private_batch_window=private_batch_window,
            alliance_batch_window=alliance_batch_window,
        ),
        logger=lambda _line: None,
        now=now or __import__("time").time,
    )
    loop.set_runtime_availability(adb_connected=True, ai_available=True)
    return loop, agent, executor


class PlayerLoopTests(unittest.TestCase):
    def test_no_event_returns_wait(self) -> None:
        loop, _agent, executor = make_loop(capture=FakeCapture())
        decision = loop.run_once()
        self.assertEqual(decision.decision_type.value, "WAIT")
        self.assertEqual(executor.actions, [])

    def test_chat_message_creates_goal_and_preview_never_sends(self) -> None:
        loop, agent, executor = make_loop(capture=FakeCapture([message(1)]))
        loop.run_once()

        self.assertEqual(len(agent.processed), 1)
        self.assertEqual(agent.discarded, ["reply-1"])
        self.assertEqual(agent.sent, [])
        self.assertEqual(executor.actions, [])
        self.assertEqual(loop.goal_manager.snapshot()[0]["status"], GoalStatus.COMPLETED.value)

    def test_duplicate_pm_events_merge_to_one_goal(self) -> None:
        loop, agent, _executor = make_loop(capture=FakeCapture([message(1), message(2)]))
        loop.run_once()

        goals = loop.goal_manager.snapshot()
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["status"], GoalStatus.COMPLETED.value)
        self.assertEqual(len(agent.processed), 1)
        self.assertIn("hello", agent.processed[0].text)

    def test_unknown_screen_does_not_execute_risky_action(self) -> None:
        loop, _agent, executor = make_loop(capture=FakeCapture(), screen="UNKNOWN")
        goal = loop.goal_manager.add_goal(Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT))
        decision = loop.run_once()

        self.assertEqual(decision.decision_type.value, "WAIT")
        self.assertEqual(goal.status, GoalStatus.PENDING)
        self.assertEqual(executor.actions, [])

    def test_completed_action_completes_goal(self) -> None:
        loop, _agent, executor = make_loop(capture=FakeCapture())
        goal = loop.goal_manager.add_goal(Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT))
        loop.run_once()

        self.assertEqual(executor.actions[0]["action"], "open_chat")
        self.assertEqual(goal.status, GoalStatus.COMPLETED)

    def test_failed_action_retries_then_fails(self) -> None:
        executor = FakeExecutor(ok=False, status="blocked")
        loop, _agent, _ = make_loop(capture=FakeCapture(), executor=executor)
        goal = loop.goal_manager.add_goal(
            Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT, max_attempts=2)
        )
        loop.run_once()
        self.assertEqual(goal.status, GoalStatus.PENDING)
        self.assertEqual(goal.attempts, 1)
        loop.run_once()
        self.assertEqual(goal.status, GoalStatus.FAILED)
        self.assertEqual(goal.attempts, 2)

    def test_auto_send_uses_existing_executor_path(self) -> None:
        loop, agent, executor = make_loop(
            capture=FakeCapture([message(8)]), auto_send=True, screen=None
        )
        loop.world_state.update_screen("PRIVATE_CONVERSATION")
        loop.world_state.set_current_conversation_verified(
            uid="1349515331000772",
            room_id="custom_1349515331000772_1224463480000772_v2",
        )
        loop.run_once()

        self.assertEqual(executor.actions[0]["action"], "reply_private")
        self.assertEqual(agent.sent, ["reply-1"])
        self.assertEqual(loop.goal_manager.snapshot()[0]["status"], GoalStatus.COMPLETED.value)

    def test_recorder_writes_event_decision_and_action_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "player-loop"
            loop, _agent, _executor = make_loop(
                capture=FakeCapture(),
                recorder=PlayerLoopRecorder(root),
            )
            loop.config.record_events = True
            loop.goal_manager.add_goal(
                Goal(goal_type=GoalType.CHECK_PRIVATE_CHAT, priority=GoalPriority.HIGH)
            )
            loop.run_once()

            for name in ("events.jsonl", "decisions.jsonl", "actions.jsonl"):
                path = root / name
                self.assertTrue(path.is_file(), name)
                self.assertTrue(path.read_text(encoding="utf-8").strip(), name)

    def test_original_language_text_is_preserved(self) -> None:
        from archive.legacy_runtime.player_loop import _chat_message_from_payload

        restored = _chat_message_from_payload({
            "room_id": "custom_1349515331000772_1224463480000772_v2",
            "group": "custom",
            "sender_uid": "1349515331000772",
            "text": "hello",
            "original_lang": "en",
        })
        self.assertEqual(restored.original_lang, "en")

    def test_alliance_casual_message_is_ignored_without_send(self) -> None:
        loop, agent, executor = make_loop(
            capture=FakeCapture([message(21, "lol", group="alliance")]),
        )
        loop.run_once()
        self.assertEqual(agent.processed, [])
        self.assertEqual(executor.actions, [])
        self.assertEqual(loop.goal_manager.snapshot(), [])

    def test_direct_alliance_question_generates_preview_only(self) -> None:
        loop, agent, executor = make_loop(
            capture=FakeCapture([message(22, "PEva are you joining?", group="alliance")]),
        )
        loop.run_once()
        self.assertEqual(len(agent.processed), 1)
        self.assertEqual(agent.processed[0].group, "alliance")
        self.assertEqual(executor.actions, [])
        self.assertEqual(loop.goal_manager.snapshot()[0]["status"], GoalStatus.COMPLETED.value)

    def test_three_private_messages_in_batch_create_one_reply(self) -> None:
        clock = [100.0]
        loop, agent, executor = make_loop(
            capture=FakeCapture([
                message(31, "hey"),
                message(32, "are you going tyrant?"),
                message(33, "we start in 10?"),
            ]),
            private_batch_window=1.0,
            now=lambda: clock[0],
        )
        loop.run_once()
        self.assertEqual(agent.processed, [])
        clock[0] = 101.1
        loop.run_once()
        self.assertEqual(len(agent.processed), 1)
        self.assertIn("are you going tyrant?", agent.processed[0].text)
        self.assertIn("we start in 10?", agent.processed[0].text)
        self.assertEqual(executor.actions, [])

    def test_self_message_is_ignored(self) -> None:
        loop, agent, _executor = make_loop(
            capture=FakeCapture([
                message(40, "PEva test", sender_uid="1224463480000772"),
            ]),
        )
        loop.run_once()
        self.assertEqual(agent.processed, [])

    def test_social_recorder_writes_analysis_and_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "player-loop"
            loop, _agent, _executor = make_loop(
                capture=FakeCapture([message(50, "lol", group="alliance")]),
                recorder=PlayerLoopRecorder(root),
            )
            loop.config.record_events = True
            loop.run_once()
            for name in ("message_analysis.jsonl", "social_decisions.jsonl"):
                self.assertTrue((root / name).read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
