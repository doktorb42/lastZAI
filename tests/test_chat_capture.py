from __future__ import annotations

import json
import unittest

from archive.legacy_runtime.chat_capture import ChatCapture, ChatCaptureConfig


SELF_UID = "1224463480000772"
PEER_UID = "1349515331000772"
ROOM_ID = f"custom_{PEER_UID}_{SELF_UID}_v2"


def callback(*, command: str = "push.chat.up.room", room_id: str = ROOM_ID, sender: str = PEER_UID) -> str:
    payload = {
        "cmd": command,
        "data": {
            "roomId": room_id,
            "group": "custom",
            "sender": sender,
            "seqId": 72,
            "msg": "hello",
            "senderInfo": {"userName": "Alex"},
        },
    }
    return "I Unity : [LUA][callback] " + json.dumps(payload)


class ChatCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = ChatCapture(ChatCaptureConfig(self_uid=SELF_UID))

    def test_accepts_current_and_legacy_push_callback_names(self) -> None:
        current = self.capture.parse_logcat_line(callback())
        self.assertIsNotNone(current)
        self.assertEqual(current.text, "hello")
        legacy = self.capture.parse_logcat_line(callback(command="push.chat.room"))
        self.assertIsNotNone(legacy)

    def test_rejects_public_custom_room_and_own_messages(self) -> None:
        self.assertIsNone(self.capture.parse_logcat_line(callback(room_id="custom_lang_en_769")))
        self.assertIsNone(self.capture.parse_logcat_line(callback(sender=SELF_UID)))

    def test_bad_callback_json_is_counted_without_crashing_listener(self) -> None:
        self.assertIsNone(self.capture.parse_logcat_line("I Unity : [LUA][callback] {bad"))
        self.assertEqual(self.capture.total_parse_errors, 1)


if __name__ == "__main__":
    unittest.main()
