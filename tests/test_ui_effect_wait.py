from __future__ import annotations

"""Regression tests for asynchronous UI settling after navigation taps."""

import unittest
from types import SimpleNamespace

from archive.legacy_runtime.chat_capture import ChatMessage
from archive.legacy_runtime.goal_manager import GoalStatus
from archive.legacy_runtime.navigation_manager import NavigationManager
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig


SELF_UID = "1224463480000772"
PEER_UID = "1349515331000772"
ROOM_ID = f"custom_{PEER_UID}_{SELF_UID}_v2"


class _Capture:
    def __init__(self) -> None:
        self.messages = [
            ChatMessage(
                room_id=ROOM_ID,
                group="custom",
                sender_uid=PEER_UID,
                sender_name="Alex",
                text="hello",
                seq_id=1,
            )
        ]

    def get_message(self, timeout: float = 0) -> ChatMessage | None:
        del timeout
        return self.messages.pop(0) if self.messages else None


class _Pending:
    reply_id = "reply-effect-1"
    player_uid = PEER_UID
    room_id = ROOM_ID
    text = "I am here."


class _Agent:
    def __init__(self) -> None:
        self.pending: dict[str, _Pending] = {}
        self.sent: list[str] = []

    def process_chat_message(self, _message: ChatMessage, **_kwargs: object) -> SimpleNamespace:
        pending = _Pending()
        self.pending[pending.reply_id] = pending
        return SimpleNamespace(status="reply_ready", reply_ready=True, pending_reply=pending)

    def get_pending_reply(self, reply_id: str) -> _Pending | None:
        return self.pending.get(reply_id)

    def mark_reply_sent(self, reply_id: str) -> None:
        self.sent.append(reply_id)
        self.pending.pop(reply_id, None)


class _Executor:
    def __init__(self, *, missing_prepare_click: bool = False) -> None:
        self.actions: list[dict[str, object]] = []
        self.missing_prepare_click = missing_prepare_click

    def execute(self, action: dict[str, object], _state: dict[str, object]) -> SimpleNamespace:
        self.actions.append(dict(action))
        if action["action"] == "prepare_private_reply_target":
            if self.missing_prepare_click:
                return SimpleNamespace(
                    ok=False,
                    status="target_navigation_failed",
                    data={
                        "delivery_state": "FAILED_SAFE",
                        "navigation_verified": False,
                        "target_uid_verified": False,
                        "room_verified": True,
                        "reason": "click_not_registered",
                    },
                )
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
                    "server_echo_seq": 41,
                    "reason": "server_echo_confirmed",
                },
            )
        return SimpleNamespace(ok=True, status="executed", data={})


class UIEffectWaitTests(unittest.TestCase):
    def build_loop(
        self,
        screens: list[str],
        *,
        effect_timeout: float = 0.75,
        missing_prepare_click: bool = False,
    ):
        clock = [100.0]
        lines: list[str] = []
        executor = _Executor(missing_prepare_click=missing_prepare_click)
        agent = _Agent()

        def provider() -> dict[str, object]:
            return {
                "screen": screens.pop(0) if screens else "PRIVATE_CONVERSATION",
                "confidence": 0.95,
                "chat_shell_evidence": True,
                "unread": {},
            }

        navigator = NavigationManager(
            now=lambda: clock[0],
            ui_effect_timeouts={
                "open_chat": effect_timeout,
                "open_private_chat": effect_timeout,
                "recover_back": effect_timeout,
                "prepare_private_reply_target": effect_timeout,
            },
            block_grace_seconds=0.40,
        )
        loop = PlayerLoop(
            capture=_Capture(),
            agent=agent,
            executor=executor,
            screen_provider=provider,
            navigation_manager=navigator,
            config=PlayerLoopConfig(
                auto_send=True,
                private_batch_window=0,
                screen_poll_interval=0.20,
                ui_effect_poll_interval=0.20,
                recovery_retry_delay=0.20,
                enable_terminal_decision_control=False,
            ),
            logger=lines.append,
            now=lambda: clock[0],
        )
        loop.set_runtime_availability(adb_connected=True, ai_available=True)
        return loop, executor, agent, clock, lines

    @staticmethod
    def tick(loop: PlayerLoop, clock: list[float], count: int = 1) -> None:
        for _ in range(count):
            clock[0] += 0.20
            loop.run_once()

    def test_open_chat_waits_through_two_stale_world_frames(self) -> None:
        loop, executor, _agent, clock, lines = self.build_loop(
            ["WORLD_MAP", "WORLD_MAP", "WORLD_MAP", "PRIVATE_CONVERSATION"]
        )
        loop.run_once()  # generates reply and taps open_chat once
        self.tick(loop, clock, 2)  # stale frames: observation only
        self.assertEqual([item["action"] for item in executor.actions], ["open_chat"])
        self.tick(loop, clock)  # conversation result -> replan as wrong/unverified
        self.assertEqual([item["action"] for item in executor.actions], ["open_chat", "recover_back"])
        self.assertTrue(any(line.startswith("[UI EFFECT CONFIRMED]") for line in lines))

    def test_recover_back_is_not_double_fired_while_first_frame_is_stale(self) -> None:
        loop, executor, _agent, clock, _lines = self.build_loop(
            ["PRIVATE_CONVERSATION", "PRIVATE_CONVERSATION", "PRIVATE_CHAT"]
        )
        loop.run_once()  # first and only Back
        self.tick(loop, clock)  # stale PRIVATE_CONVERSATION
        self.assertEqual([item["action"] for item in executor.actions], ["recover_back"])
        self.tick(loop, clock)  # PRIVATE_CHAT confirms effect
        self.assertEqual([item["action"] for item in executor.actions], ["recover_back"])

    def test_private_tab_waits_through_unclassified_frames_without_blocking(self) -> None:
        loop, executor, _agent, clock, _lines = self.build_loop(
            ["CHAT_OPEN_UNCLASSIFIED", "CHAT_OPEN_UNCLASSIFIED", "CHAT_OPEN_UNCLASSIFIED", "PRIVATE_CHAT", "PRIVATE_CHAT"]
        )
        loop.run_once()  # tap private tab
        self.tick(loop, clock, 2)  # same shell, no second tap
        self.assertEqual([item["action"] for item in executor.actions], ["open_private_chat"])
        self.tick(loop, clock, 2)  # PRIVATE_CHAT becomes stable then row selection
        self.assertEqual([item["action"] for item in executor.actions], ["open_private_chat", "prepare_private_reply_target"])
        self.assertNotEqual(loop.goal_manager.snapshot()[0]["status"], GoalStatus.BLOCKED.value)

    def test_effect_timeout_counts_one_failure_then_block_grace(self) -> None:
        loop, executor, _agent, clock, lines = self.build_loop(["WORLD_MAP"] * 20)
        loop.run_once()
        self.tick(loop, clock, 4)  # opens once, waits through timeout, enters grace
        self.assertEqual([item["action"] for item in executor.actions], ["open_chat"])
        self.assertTrue(any(line.startswith("[UI EFFECT TIMEOUT]") for line in lines))
        self.assertNotEqual(loop.goal_manager.snapshot()[0]["status"], GoalStatus.BLOCKED.value)
        self.tick(loop, clock, 3)
        self.assertEqual(loop.goal_manager.snapshot()[0]["status"], GoalStatus.BLOCKED.value)

    def test_block_grace_accepts_late_meaningful_transition(self) -> None:
        loop, executor, _agent, clock, _lines = self.build_loop(
            ["WORLD_MAP", "WORLD_MAP", "WORLD_MAP", "WORLD_MAP", "ALLIANCE_CHAT"]
        )
        loop.run_once()
        self.tick(loop, clock, 4)  # timeout and candidate grace
        self.tick(loop, clock)  # ALLIANCE_CHAT arrives during grace
        self.assertNotEqual(loop.goal_manager.snapshot()[0]["status"], GoalStatus.BLOCKED.value)
        self.assertEqual([item["action"] for item in executor.actions][:2], ["open_chat", "open_private_chat"])

    def test_private_rows_need_two_stable_list_observations(self) -> None:
        loop, executor, _agent, clock, _lines = self.build_loop(
            ["PRIVATE_CHAT", "PRIVATE_CHAT", "PRIVATE_CHAT"]
        )
        loop.run_once()  # first stable sample only
        self.assertEqual(executor.actions, [])
        self.tick(loop, clock)
        self.assertEqual([item["action"] for item in executor.actions], ["prepare_private_reply_target"])

    def test_missing_uid_click_then_conversation_is_unverified_and_recovers(self) -> None:
        loop, executor, agent, clock, lines = self.build_loop(
            ["PRIVATE_CHAT", "PRIVATE_CHAT", "PRIVATE_CONVERSATION"],
            missing_prepare_click=True,
        )
        loop.run_once()  # first stable list sample
        self.tick(loop, clock)  # row tap has no matching new click event
        self.tick(loop, clock)  # visual conversation change remains unverified
        self.assertEqual(
            [item["action"] for item in executor.actions],
            ["prepare_private_reply_target", "recover_back"],
        )
        self.assertEqual(agent.sent, [])
        self.assertTrue(any(
            "classification=unverified_conversation_after_click_not_registered" in line
            for line in lines
        ))

    def test_parent_goal_remains_visible_while_effect_is_pending(self) -> None:
        loop, _executor, _agent, clock, _lines = self.build_loop(["WORLD_MAP", "WORLD_MAP"])
        loop.run_once()
        waiting = loop.goal_manager.get_waiting_goal()
        self.assertIsNotNone(waiting)
        self.assertEqual(waiting.goal_id, loop.goal_manager.snapshot()[0]["goal_id"])
        self.assertEqual(waiting.payload["pending_reply_text"], "I am here.")
        task = waiting.payload["navigation_task"]
        self.assertTrue(task["ui_effect_active"])
        self.assertEqual(waiting.waiting_reason, "WAITING_FOR_UI_EFFECT:navigation_action")


if __name__ == "__main__":
    unittest.main()
