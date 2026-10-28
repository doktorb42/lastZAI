from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mozg.conversation_store import ConversationStore
from mozg.message_intent import MessageIntentAnalyzer
from charakter.social_context import Relationship
from charakter.social_policy import SocialPolicy


def message(text: str, group: str = "alliance", uid: str = "player-1") -> dict[str, object]:
    return {"text": text, "group": group, "sender_uid": uid, "sender_name": "Gio"}


class SocialPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = [100.0]
        self.policy = SocialPolicy(now=lambda: self.clock[0])
        self.analyzer = MessageIntentAnalyzer()

    def test_pm_replies_but_casual_alliance_does_not(self) -> None:
        pm = message("hello", "custom")
        alliance = message("lol")
        self.assertTrue(self.policy.evaluate(self.analyzer.analyze(pm), pm).should_reply)
        self.assertFalse(self.policy.evaluate(self.analyzer.analyze(alliance), alliance).should_reply)

    def test_direct_alliance_question_replies_but_announcement_does_not(self) -> None:
        question = message("PEva are you joining?")
        announcement = message("everyone shield after reset")
        self.assertTrue(self.policy.evaluate(self.analyzer.analyze(question), question).should_reply)
        self.assertFalse(self.policy.evaluate(self.analyzer.analyze(announcement), announcement).should_reply)

    def test_cooldown_blocks_repeated_player_reply(self) -> None:
        item = message("PEva check pm")
        analysis = self.analyzer.analyze(item)
        self.assertTrue(self.policy.evaluate(analysis, item).should_reply)
        self.policy.note_reply(group="alliance", sender_uid="player-1")
        blocked = self.policy.evaluate(analysis, item)
        self.assertFalse(blocked.should_reply)
        self.assertIn("cooldown", blocked.reason.lower())
        self.clock[0] += 16.0
        self.assertTrue(self.policy.evaluate(analysis, item).should_reply)

    def test_player_profile_is_additive_and_relationship_is_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with ConversationStore(Path(tmp) / "social.db") as store:
                profile = store.observe_social_message(
                    uid="player-42", name="Alex", alliance="ROXY", server_id=772,
                    group="alliance", seen_at=1234,
                )
                self.assertEqual(profile.messages_received, 1)
                updated = store.set_player_relationship("player-42", Relationship.OFFICER)
                self.assertEqual(updated.relationship, Relationship.OFFICER)
                self.assertEqual(store._conn.execute("PRAGMA integrity_check;").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
