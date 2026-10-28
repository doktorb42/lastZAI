from __future__ import annotations

import unittest

from charakter.social_context import AllianceConversationContext


class AllianceContextTests(unittest.TestCase):
    def test_context_keeps_only_bounded_recent_messages(self) -> None:
        context = AllianceConversationContext(max_messages=3)
        for index in range(4):
            self.assertTrue(context.add_message({
                "sender_uid": f"p{index}",
                "sender_name": f"Player {index}",
                "text": f"message {index}",
                "source_message_key": f"alliance:{index}",
            }, timestamp=float(index)))
        self.assertEqual(len(context), 3)
        self.assertEqual([item.text for item in context.recent_messages(10)], ["message 1", "message 2", "message 3"])
        self.assertEqual(len(context.render_recent(2)), 2)

    def test_context_deduplicates_source_keys(self) -> None:
        context = AllianceConversationContext()
        item = {"sender_uid": "p1", "sender_name": "Gio", "text": "PEva coming?", "source_message_key": "a:1"}
        self.assertTrue(context.add_message(item))
        self.assertFalse(context.add_message(item))
        self.assertEqual(len(context), 1)


if __name__ == "__main__":
    unittest.main()
