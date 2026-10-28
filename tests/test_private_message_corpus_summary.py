from __future__ import annotations

import unittest

from trening.private_message_corpus_summary import summarize_message_records
from charakter.social_reply_policy import PrivateSocialReplyPolicy


class _Analyzer:
    def analyze(self, message: dict[str, str], **_kwargs: object) -> dict[str, object]:
        text = message["text"]
        if "attack" in text:
            return {"group": "custom", "intent": "ACTION_REQUEST"}
        if text == "got it, thanks":
            return {"group": "custom", "intent": "THANKS"}
        return {"group": "custom", "intent": "QUESTION", "is_question": True}


class PrivateMessageCorpusSummaryTests(unittest.TestCase):
    def test_summary_only_returns_aggregate_information(self) -> None:
        result = summarize_message_records(
            [
                {"direction": "incoming", "role": "user", "text": "hey, what are you doing?", "room_id": "custom_1224463480000772_1349515331000772_v2", "server_time": 1791933956716},
                {"direction": "incoming", "role": "user", "text": "got it, thanks", "room_id": "custom_1224463480000772_1349515331000772_v2", "server_time": 1791933957716},
                {"direction": "incoming", "role": "user", "text": "can you attack now?", "room_id": "custom_1224463480000772_1349515331000772_v2", "server_time": 1791933958716},
                {"direction": "outgoing", "role": "assistant", "text": "hidden reply", "room_id": "custom_1224463480000772_1349515331000772_v2", "server_time": 1791933959716},
            ],
            analyzer=_Analyzer(),
            policy=PrivateSocialReplyPolicy(),
        )

        self.assertEqual(result["counts"]["incoming_private_messages"], 3)
        self.assertEqual(result["counts"]["outgoing_assistant_messages"], 1)
        self.assertEqual(result["social_preview_policy"]["eligible_preview_count"], 1)
        self.assertEqual(result["social_preview_policy"]["kept_silent_count"], 2)
        self.assertNotIn("hey, what are you doing?", str(result))
        self.assertNotIn("hidden reply", str(result))


if __name__ == "__main__":
    unittest.main()
