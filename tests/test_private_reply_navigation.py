from __future__ import annotations

import unittest
from types import SimpleNamespace

from archive.legacy_runtime.chat_capture import ChatMessage
from archive.legacy_runtime.decision_engine import DecisionEngine, DecisionType
from archive.legacy_runtime.goal_manager import Goal, GoalStatus, GoalType
from archive.legacy_runtime.navigation_manager import NavigationManager
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig
from archive.legacy_runtime.world_state import WorldState


SELF_UID = "1224463480000772"
PEER_UID = "1349515331000772"
ROOM_ID = f"custom_{PEER_UID}_{SELF_UID}_v2"
REPLY_TEXT = "I'm doing fine, thanks for asking."


class _Pending:
    def __init__(self, message: ChatMessage) -> None:
        self.reply_id = "reply-live-1"
        self.player_uid = message.sender_uid
        self.room_id = message.room_id
        self.text = REPLY_TEXT


class _Agent:
    def __init__(self) -> None:
        self.pending: dict[str, _Pending] = {}
        self.sent: list[str] = []
        self.generated = 0

    def process_chat_message(self, message: ChatMessage, **_kwargs: object) -> SimpleNamespace:
        self.generated += 1
        pending = _Pending(message)
        self.pending[pending.reply_id] = pending
        return SimpleNamespace(status="reply_ready", reply_ready=True, pending_reply=pending)

    def get_pending_reply(self, reply_id: str) -> _Pending | None:
        return self.pending.get(reply_id)

    def mark_reply_sent(self, reply_id: str) -> None:
        self.sent.append(reply_id)
        self.pending.pop(reply_id, None)

    def discard_reply(self, reply_id: str) -> None:
        self.pending.pop(reply_id, None)


class _Capture:
    def __init__(self, messages: list[ChatMessage] | None = None) -> None:
        self.messages = list(messages or [])

    def get_message(self, timeout: float = 0) -> ChatMessage | None:
        del timeout
        return self.messages.pop(0) if self.messages else None


class _Executor:
    def __init__(self) -> None:
        self.actions: list[dict[str, object]] = []

    def execute(self, action: dict[str, object], _state: object) -> SimpleNamespace:
        self.actions.append(dict(action))
        name = action["action"]
        if name == "prepare_private_reply_target":
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
        if name == "reply_private":
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
                    "server_echo_seq": 99,
                    "reason": "server_echo_confirmed",
                },
            )
        return SimpleNamespace(ok=True, status="executed", data={})


def _message() -> ChatMessage:
    return ChatMessage(
        room_id=ROOM_ID,
        group="custom",
        sender_uid=PEER_UID,
        sender_name="Alex",
        text="how are you?",
        seq_id=73,
    )


def _pending_goal() -> Goal:
    return Goal(
        goal_type=GoalType.ANSWER_PRIVATE_MESSAGE,
        payload={
            "sender_uid": PEER_UID,
            "room_id": ROOM_ID,
            "pending_reply_id": "reply-live-1",
        },
    )


def _state(screen: str) -> WorldState:
    state = WorldState(adb_connected=True, ai_available=True)
    state.update_screen(screen)
    return state


class PrivateReplyNavigationDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = DecisionEngine()
        self.navigator = NavigationManager(now=lambda: 100.0)

    def _plan(self, screen: str) -> object:
        return self.navigator.plan_private_conversation(
            _pending_goal(), _state(screen), observed_this_tick=True
        )

    def test_world_map_pending_reply_opens_chat_without_send(self) -> None:
        plan = self._plan("WORLD_MAP")
        self.assertEqual(plan.kind, "ACTION")
        self.assertEqual(plan.action, "open_chat")

    def test_hq_pending_reply_opens_chat(self) -> None:
        plan = self._plan("HQ_BASE")
        self.assertEqual(plan.kind, "ACTION")
        self.assertEqual(plan.action, "open_chat")

    def test_alliance_chat_pending_reply_opens_private_tab(self) -> None:
        plan = self._plan("ALLIANCE_CHAT")
        self.assertEqual(plan.kind, "ACTION")
        self.assertEqual(plan.action, "open_private_chat")

    def test_private_chat_pending_reply_prepares_verified_target_not_send(self) -> None:
        goal = _pending_goal()
        state = _state("PRIVATE_CHAT")
        state.chat_shell_evidence = True
        first = self.navigator.plan_private_conversation(goal, state, observed_this_tick=True)
        self.assertEqual((first.kind, first.reason), ("WAIT", "private_chat_stability"))
        plan = self.navigator.plan_private_conversation(goal, state, observed_this_tick=True)
        self.assertEqual(plan.kind, "ACTION")
        self.assertEqual(plan.action, "prepare_private_reply_target")

    def test_verified_matching_private_conversation_sends_directly(self) -> None:
        state = _state("PRIVATE_CONVERSATION")
        state.set_current_conversation_verified(uid=PEER_UID, room_id=ROOM_ID)
        plan = self.navigator.plan_private_conversation(
            _pending_goal(), state, observed_this_tick=True
        )
        self.assertEqual(plan.kind, "TARGET_REACHED")


class PrivateReplyNavigationLoopTests(unittest.TestCase):
    def _loop(self, screens: list[str], *, max_retries: int = 3) -> tuple[PlayerLoop, _Executor, _Agent, list[float], list[str]]:
        clock = [100.0]
        lines: list[str] = []
        executor = _Executor()
        agent = _Agent()

        def screen_provider() -> dict[str, object]:
            screen = screens.pop(0) if screens else "WORLD_MAP"
            return {"screen": screen, "unread": {}}

        loop = PlayerLoop(
            capture=_Capture([_message()]),
            agent=agent,
            executor=executor,
            screen_provider=screen_provider,
            config=PlayerLoopConfig(
                auto_send=True,
                private_batch_window=0,
                screen_poll_interval=0.1,
                recovery_retry_delay=0.1,
                max_action_retries=max_retries,
                enable_terminal_decision_control=False,
            ),
            logger=lines.append,
            now=lambda: clock[0],
        )
        loop.set_runtime_availability(adb_connected=True, ai_available=True)
        return loop, executor, agent, clock, lines

    def test_unchanged_world_map_times_out_navigation_without_reply_transport(self) -> None:
        loop, executor, _agent, clock, _lines = self._loop(["WORLD_MAP"] * 5, max_retries=2)
        for _ in range(4):
            loop.run_once()
            clock[0] += 1

        self.assertEqual([item["action"] for item in executor.actions], ["open_chat"])
        self.assertFalse(any(item["action"] == "reply_private" for item in executor.actions))

    def test_navigation_exhaustion_blocks_once_without_send_or_user_decision(self) -> None:
        loop, executor, _agent, clock, _lines = self._loop(["WORLD_MAP"] * 6, max_retries=2)
        for _ in range(4):
            loop.run_once()
            clock[0] += 1

        goal = loop.goal_manager.snapshot()[0]
        self.assertEqual(goal["status"], GoalStatus.BLOCKED.value)
        self.assertTrue(goal["blocked_reason"].startswith("BLOCKED_BY_NAVIGATION:"))
        self.assertFalse(any(item["action"] in {"reply_private", "prepare_private_reply_target"} for item in executor.actions))
        self.assertIsNone(loop.user_decision_manager.pending_delivery())

    def test_successive_navigation_polls_preserve_original_pending_reply(self) -> None:
        loop, executor, agent, clock, _lines = self._loop(
            ["WORLD_MAP", "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION"]
        )
        for _ in range(5):
            loop.run_once()
            clock[0] += 0.25

        self.assertEqual(
            [item["action"] for item in executor.actions],
            ["open_chat", "open_private_chat", "prepare_private_reply_target", "reply_private"],
        )
        send = executor.actions[-1]
        self.assertEqual(send["reply_id"], "reply-live-1")
        self.assertEqual(send["text"], REPLY_TEXT)
        self.assertEqual(agent.generated, 1)
        self.assertEqual(agent.sent, ["reply-live-1"])

    def test_live_trace_navigates_before_confirmed_send(self) -> None:
        loop, executor, agent, clock, lines = self._loop(
            ["WORLD_MAP", "ALLIANCE_CHAT", "PRIVATE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION"]
        )
        for _ in range(5):
            loop.run_once()
            clock[0] += 0.25

        actions = [item["action"] for item in executor.actions]
        self.assertEqual(actions[-1], "reply_private")
        self.assertEqual(actions[:3], ["open_chat", "open_private_chat", "prepare_private_reply_target"])
        self.assertEqual(agent.sent, ["reply-live-1"])
        self.assertTrue(any(line.startswith("[INCOMING]") and 'text="how are you?"' in line for line in lines))
        self.assertTrue(any(line.startswith("[AI REPLY]") and REPLY_TEXT in line for line in lines))
        self.assertTrue(any("next_action=open_chat" in line for line in lines))
        self.assertTrue(any("next_action=open_private_chat" in line for line in lines))
        self.assertTrue(any("action=prepare_private_reply_target" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
