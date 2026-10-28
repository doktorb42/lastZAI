from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from archive.legacy_runtime.action_ex import ActionExecutor
from archive.legacy_runtime.chat_capture import ChatMessage
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig


SELF_UID = "1224463480000772"
PEER_UID = "1349515331000772"
ROOM_ID = f"custom_{PEER_UID}_{SELF_UID}_v2"


class _Capture:
    def __init__(self, message: ChatMessage) -> None:
        self.message = message

    def get_message(self, timeout: float = 0) -> ChatMessage | None:
        del timeout
        message, self.message = self.message, None
        return message


class _PendingReply:
    def __init__(self, message: ChatMessage) -> None:
        self.reply_id = "reply-42"
        self.player_uid = message.sender_uid
        self.room_id = message.room_id
        self.text = 'final reply with "quotes"'


class _Agent:
    def __init__(self) -> None:
        self.pending: dict[str, _PendingReply] = {}

    def process_chat_message(self, message: ChatMessage, **_kwargs: object) -> SimpleNamespace:
        pending = _PendingReply(message)
        self.pending[pending.reply_id] = pending
        return SimpleNamespace(status="reply_ready", reply_ready=True, pending_reply=pending)

    def discard_reply(self, reply_id: str) -> None:
        self.pending.pop(reply_id, None)


class _UnusedExecutor:
    def execute(self, _action: object, _state: object) -> object:
        raise AssertionError("preview mode must not execute a send action")


def _message() -> ChatMessage:
    return ChatMessage(
        room_id=ROOM_ID,
        group="custom",
        sender_uid=PEER_UID,
        sender_name="Alex",
        text='incoming text with "quotes"',
        seq_id=73,
    )


def _reply_action() -> dict[str, str]:
    return {
        "action": "reply_private",
        "goal_id": "goal-9",
        "reply_id": "reply-9",
        "target_uid": PEER_UID,
        "room_id": ROOM_ID,
        "text": 'attempted text with "quotes"',
    }


class MessageContentLoggingTests(unittest.TestCase):
    def test_incoming_and_final_generated_reply_are_logged_in_preview(self) -> None:
        lines: list[str] = []
        loop = PlayerLoop(
            capture=_Capture(_message()),
            agent=_Agent(),
            executor=_UnusedExecutor(),
            config=PlayerLoopConfig(private_batch_window=0, auto_send=False),
            logger=lines.append,
        )
        loop.set_runtime_availability(adb_connected=True, ai_available=True)

        loop.run_once()

        self.assertTrue(any(
            line.startswith("[INCOMING] sender=Alex ")
            and 'text="incoming text with \\"quotes\\""' in line
            for line in lines
        ))
        self.assertTrue(any(
            line.startswith("[AI REPLY] goal=")
            and "reply_id=reply-42" in line
            and 'text="final reply with \\"quotes\\""' in line
            for line in lines
        ))
        self.assertFalse(any("[SEND CONFIRMED]" in line for line in lines))

    def test_failed_send_logs_the_attempted_text(self) -> None:
        lines: list[str] = []
        executor = ActionExecutor(logger=lines.append)
        # This models a real send tap without the required own server echo.
        with patch(
            "archive.legacy_runtime.action_ex.adb_controll.send_private_reply_verified",
            return_value={
                "confirmed": False,
                "send_tap_attempted": True,
                "server_echo_seen": False,
                "delivery_state": "AMBIGUOUS",
                "reason": "server_echo_not_seen_after_send_tap",
            },
        ):
            result = executor.execute(_reply_action(), {
                "screen": "PRIVATE_CONVERSATION",
                "current_conversation_verified": True,
                "current_conversation_uid": PEER_UID,
                "current_conversation_room_id": ROOM_ID,
            })

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "delivery_ambiguous")
        self.assertTrue(any(
            line.startswith("[SEND ATTEMPT]")
            and 'text="attempted text with \\"quotes\\""' in line
            for line in lines
        ))
        self.assertTrue(any(
            line.startswith("[SEND FAILED]")
            and 'text="attempted text with \\"quotes\\""' in line
            for line in lines
        ))
        self.assertFalse(any("[SEND CONFIRMED]" in line for line in lines))

    def test_confirmed_send_logs_only_server_echo_confirmed_text(self) -> None:
        lines: list[str] = []
        executor = ActionExecutor(logger=lines.append)
        with patch(
            "archive.legacy_runtime.action_ex.adb_controll.send_private_reply_verified",
            return_value={
                "confirmed": True,
                "seq_id": 321,
                "server_echo_seq": 321,
                "server_echo_seen": True,
                "send_tap_attempted": True,
                "delivery_state": "CONFIRMED",
                "reason": "server_echo_confirmed",
            },
        ):
            result = executor.execute(_reply_action(), {
                "screen": "PRIVATE_CONVERSATION",
                "current_conversation_verified": True,
                "current_conversation_uid": PEER_UID,
                "current_conversation_room_id": ROOM_ID,
            })

        self.assertTrue(result.ok)
        self.assertTrue(any(
            line.startswith("[SEND CONFIRMED] goal=goal-9 reply_id=reply-9 ")
            and 'text="attempted text with \\"quotes\\"" seq=321' in line
            for line in lines
        ))

    def test_unverified_world_screen_never_calls_private_transport_or_logs_attempt(self) -> None:
        lines: list[str] = []
        executor = ActionExecutor(logger=lines.append)
        with patch("archive.legacy_runtime.action_ex.adb_controll.send_private_reply_verified") as sender:
            result = executor.execute(_reply_action(), {"screen": "WORLD_MAP"})

        self.assertFalse(result.ok)
        sender.assert_not_called()
        self.assertFalse(any(line.startswith("[SEND ATTEMPT]") for line in lines))
        self.assertTrue(any(line.startswith("[SEND FAILED]") for line in lines))


if __name__ == "__main__":
    unittest.main()
