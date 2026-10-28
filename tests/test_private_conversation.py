from __future__ import annotations

import unittest
from types import SimpleNamespace

from archive.legacy_runtime.chat_capture import ChatMessage
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig
from mozg.private_context import PrivateContextRegistry


class _Capture:
    def __init__(self, messages: list[ChatMessage]) -> None:
        self.messages = list(messages)

    def get_message(self, timeout: float = 0) -> ChatMessage | None:
        return self.messages.pop(0) if self.messages else None


class _Pending:
    def __init__(self, reply_id: str, message: ChatMessage) -> None:
        self.reply_id = reply_id
        self.text = (
            "done" if "reinforce" in message.text
            else ("yes" if "you did it" in message.text else "yeah 10 min works")
        )
        self.player_uid = message.sender_uid
        self.room_id = message.room_id


class _Agent:
    def __init__(self) -> None:
        self.pending: dict[str, _Pending] = {}
        self.last_pending: _Pending | None = None

    def process_chat_message(self, message: ChatMessage, **_kwargs: object) -> SimpleNamespace:
        pending = _Pending(f"r{len(self.pending) + 1}", message)
        self.pending[pending.reply_id] = pending
        self.last_pending = pending
        return SimpleNamespace(status="reply_ready", reply_ready=True, pending_reply=pending)

    def replace_reply_text(self, reply_id: str, text: str) -> bool:
        self.pending[reply_id].text = text
        return True

    def discard_reply(self, reply_id: str) -> None:
        self.pending.pop(reply_id, None)


class _Executor:
    def __init__(self) -> None:
        self.actions: list[object] = []

    def execute(self, action: object, _state: object) -> object:
        self.actions.append(action)
        return SimpleNamespace(ok=True, status="sent")


def pm(seq: int, text: str) -> ChatMessage:
    return ChatMessage(
        room_id="custom_p1_1224463480000772_v2", group="custom", sender_uid="p1",
        sender_name="Alex", text=text, seq_id=seq,
    )


class PrivateConversationTests(unittest.TestCase):
    def test_context_retains_follow_up_state(self) -> None:
        registry = PrivateContextRegistry()
        context = registry.get_or_create(player_uid="p1", room_id="r1")
        context.add_user_message({"text": "I work tomorrow morning"}, timestamp=10)
        context.add_peva_reply("damn early shift?", timestamp=11)
        context.add_user_message({"text": "yeah"}, timestamp=12)
        self.assertTrue(context.waiting_for_followup)
        self.assertIn("early shift", context.render_relevant(limit=10)[1])

    def test_one_private_batch_gets_one_preview_and_no_action(self) -> None:
        clock = [100.0]
        agent = _Agent()
        executor = _Executor()
        loop = PlayerLoop(
            capture=_Capture([pm(1, "hey"), pm(2, "you joining tyrant?"), pm(3, "we start in 10")]),
            agent=agent, executor=executor,
            config=PlayerLoopConfig(private_batch_window=1.1),
            logger=lambda _line: None, now=lambda: clock[0],
        )
        loop.run_once()
        self.assertIsNone(agent.last_pending)
        clock[0] = 101.2
        loop.run_once()
        self.assertIsNotNone(agent.last_pending)
        self.assertEqual(executor.actions, [])
        context = loop.private_contexts.get(player_uid="p1", room_id="custom_p1_1224463480000772_v2")
        self.assertIsNotNone(context)
        self.assertGreaterEqual(len(context.recent_messages), 4)

    def test_unexecuted_reinforce_command_is_guarded_in_preview(self) -> None:
        agent = _Agent()
        loop = PlayerLoop(
            capture=_Capture([pm(10, "reinforce me")]), agent=agent, executor=_Executor(),
            config=PlayerLoopConfig(private_batch_window=0), logger=lambda _line: None,
        )
        loop.run_once()
        self.assertIsNotNone(agent.last_pending)
        self.assertNotIn("done", agent.last_pending.text.casefold())
        self.assertNotIn("reinforced", agent.last_pending.text.casefold())
        self.assertEqual(loop.world_state.pending_command_type, "REINFORCE_PLAYER")

    def test_wait_command_sets_runtime_waiting_context(self) -> None:
        clock = [200.0]
        loop = PlayerLoop(
            capture=_Capture([pm(20, "wait 2 min")]), agent=_Agent(), executor=_Executor(),
            config=PlayerLoopConfig(private_batch_window=0), logger=lambda _line: None,
            now=lambda: clock[0],
        )
        loop.run_once()
        context = loop.private_contexts.get(player_uid="p1", room_id="custom_p1_1224463480000772_v2")
        self.assertEqual(loop.world_state.pending_command_type, "WAIT")
        self.assertAlmostEqual(context.waiting_until or 0, 320.0)

    def test_followup_after_pending_command_cannot_claim_it_happened(self) -> None:
        agent = _Agent()
        capture = _Capture([pm(30, "reinforce me")])
        loop = PlayerLoop(
            capture=capture, agent=agent, executor=_Executor(),
            config=PlayerLoopConfig(private_batch_window=0), logger=lambda _line: None,
        )
        loop.run_once()
        capture.messages.append(pm(31, "you did it?"))
        loop.run_once()
        self.assertIsNotNone(agent.last_pending)
        self.assertNotEqual(agent.last_pending.text.casefold(), "yes")
        self.assertNotIn("reinforc", agent.last_pending.text.casefold())
        self.assertTrue(agent.last_pending.text.strip())

    def test_batched_command_then_followup_keeps_action_safety_context(self) -> None:
        clock = [100.0]
        agent = _Agent()
        capture = _Capture([pm(40, "reinforce me"), pm(41, "wait 2 min"), pm(42, "my name is Alex")])
        loop = PlayerLoop(
            capture=capture, agent=agent, executor=_Executor(),
            config=PlayerLoopConfig(private_batch_window=1.0), logger=lambda _line: None,
            now=lambda: clock[0],
        )
        loop.run_once()
        goal = loop.goal_manager.get_next_goal(now=clock[0] + 10)
        self.assertEqual(len(goal.payload["parsed_commands"]), 2)
        self.assertEqual(goal.payload["parsed_commands"][0]["command_type"], "REINFORCE_PLAYER")
        clock[0] = 101.1
        loop.run_once()
        self.assertNotIn("done", agent.last_pending.text.casefold())
        self.assertNotIn("reinforc", agent.last_pending.text.casefold())
        capture.messages.append(pm(43, "you did it?"))
        loop.run_once()
        clock[0] = 102.2
        loop.run_once()
        self.assertNotEqual(agent.last_pending.text.casefold(), "yes")
        self.assertTrue(agent.last_pending.text.strip())


if __name__ == "__main__":
    unittest.main()
