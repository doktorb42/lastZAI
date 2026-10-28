from __future__ import annotations

import json
import unittest
from pathlib import Path

from mozg.message_intent import MessageIntent, MessageIntentAnalyzer


class _InvalidIntentAI:
    def complete_task_safe(self, **_kwargs: object) -> object:
        return type("Reply", (), {"text": "this is not json"})()


def payload(text: str, group: str = "alliance", uid: str = "p-1") -> dict[str, object]:
    return {
        "room_id": "alliance_772_roxy" if group == "alliance" else "custom_p-1_1224463480000772_v2",
        "group": group,
        "sender_uid": uid,
        "sender_name": "Gio",
        "text": text,
        "source_message_key": f"{uid}:{text}",
    }


class MessageIntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = MessageIntentAnalyzer()

    def test_private_message_defaults_to_reply(self) -> None:
        analysis = self.analyzer.analyze(payload("hello", "custom"))
        self.assertEqual(analysis.intent, MessageIntent.GREETING)
        self.assertTrue(analysis.requires_reply)
        self.assertEqual(analysis.reply_priority, "HIGH")

    def test_direct_question_and_request_are_addressed_to_peva(self) -> None:
        question = self.analyzer.analyze(payload("PEva are you joining?"))
        command = self.analyzer.analyze(payload("PEva reinforce me"))
        self.assertEqual(question.intent, MessageIntent.QUESTION)
        self.assertTrue(question.directed_at_peva)
        self.assertIn(command.intent, {MessageIntent.REQUEST, MessageIntent.COMMAND, MessageIntent.ACTION_REQUEST, MessageIntent.ACTION_COMMAND})
        self.assertTrue(command.directed_at_peva)

    def test_alliance_announcement_and_reactions_do_not_require_reply(self) -> None:
        announcement = self.analyzer.analyze(payload("everyone shield after reset"))
        reaction = self.analyzer.analyze(payload("😂😂"))
        casual = self.analyzer.analyze(payload("lol"))
        self.assertEqual(announcement.intent, MessageIntent.ALLIANCE_ANNOUNCEMENT)
        self.assertFalse(announcement.requires_reply)
        self.assertEqual(reaction.intent, MessageIntent.REACTION)
        self.assertEqual(casual.intent, MessageIntent.SOCIAL)

    def test_duplicate_and_self_message_are_ignored(self) -> None:
        duplicate = self.analyzer.analyze(payload("hello"), context={"duplicate": True})
        own = self.analyzer.analyze(payload("hello", uid="1224463480000772"), self_uid="1224463480000772")
        self.assertEqual(duplicate.intent, MessageIntent.IGNORE)
        self.assertEqual(own.intent, MessageIntent.IGNORE)

    def test_invalid_llm_fallback_stays_safe_for_alliance(self) -> None:
        analyzer = MessageIntentAnalyzer(ai=_InvalidIntentAI())
        analysis = analyzer.analyze(payload("hmm maybe"))
        self.assertEqual(analysis.intent, MessageIntent.UNKNOWN)
        self.assertLess(analysis.confidence, 0.7)
        self.assertFalse(analysis.requires_reply)

    def test_realistic_fixture_texts_have_classifiable_safe_outcomes(self) -> None:
        fixture = Path(__file__).with_name("fixtures") / "social_messages.json"
        items = json.loads(fixture.read_text(encoding="utf-8"))
        analyses = [self.analyzer.analyze(payload(str(item["text"]), str(item["group"]))) for item in items]
        self.assertEqual(len(analyses), len(items))
        self.assertIn(MessageIntent.ALLIANCE_ANNOUNCEMENT, {item.intent for item in analyses})


if __name__ == "__main__":
    unittest.main()
