from __future__ import annotations

import unittest

from charakter.social_reply_policy import PrivateSocialReplyPolicy


class PrivateSocialReplyPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PrivateSocialReplyPolicy()

    @staticmethod
    def analysis(intent: str, **extra: object) -> dict[str, object]:
        return {"group": "custom", "intent": intent, **extra}

    def test_social_bids_need_a_preview_reply(self) -> None:
        samples = (
            ("GREETING", "hey there"),
            ("QUESTION", "what are you doing?", {"is_question": True}),
            ("UNKNOWN", "that was petty", {}),
            ("SOCIAL", "nice one, congrats", {}),
        )
        for item in samples:
            intent, text, *extra = item
            with self.subTest(text=text):
                self.assertTrue(self.policy.evaluate(self.analysis(intent, **(extra[0] if extra else {})), text).should_generate_preview)

    def test_reactions_closures_pauses_and_actions_stay_silent(self) -> None:
        samples = (
            ("REACTION", "👍"),
            ("THANKS", "got it, thanks"),
            ("DIRECT_MESSAGE", "brb, one minute"),
            ("ACTION_REQUEST", "can you attack them now?"),
        )
        for intent, text in samples:
            with self.subTest(text=text):
                self.assertFalse(self.policy.evaluate(self.analysis(intent), text).should_generate_preview)


if __name__ == "__main__":
    unittest.main()
