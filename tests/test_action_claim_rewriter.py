from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mozg.action_claim_guard import ActionClaimGuard
from mozg.action_claim_rewriter import ActionClaimRewriter
from mozg.command_parser import CommandParser
from trening.feedback_store import FeedbackStore
from archive.legacy_runtime.goal_manager import Goal, GoalType
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig


class _RewriteAI:
    def __init__(self, text: str = "", error: str | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0

    def complete_task_safe(self, **_kwargs: object) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(text=self.text, error=self.error)


class _Agent:
    def __init__(self, ai: _RewriteAI) -> None:
        self.ai = ai


class _Recorder:
    def __init__(self) -> None:
        self.raw: list[dict[str, object]] = []

    def record_raw_training(self, payload: dict[str, object]) -> None:
        self.raw.append(dict(payload))


def _goal(command: object, *, status: str = "DETECTED") -> Goal:
    command_dict = command.to_dict()
    return Goal(
        goal_type=GoalType.ANSWER_PRIVATE_MESSAGE,
        payload={
            "sender_uid": "p1",
            "room_id": "r1",
            "group": "custom",
            "parsed_command": command_dict,
            "parsed_commands": [command_dict],
            "pending_command": {"command_type": command_dict["command_type"], "status": status},
            "pending_commands": [{"command_type": command_dict["command_type"], "status": status}],
            "messages": [],
        },
    )


def _message(text: str) -> dict[str, str]:
    return {
        "text": text,
        "sender_uid": "p1",
        "room_id": "r1",
        "source_message_key": "r1:100",
    }


class ActionClaimRewriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = CommandParser()

    def _loop(self, ai: _RewriteAI, **kwargs: object) -> PlayerLoop:
        return PlayerLoop(
            capture=None,
            agent=_Agent(ai),
            executor=object(),
            config=PlayerLoopConfig(),
            logger=lambda _line: None,
            **kwargs,
        )

    def test_safe_normal_reply_is_untouched_without_extra_model_call(self) -> None:
        ai = _RewriteAI("unused")
        command = self.parser.parse("reinforce me", sender_uid="p1")
        result = self._loop(ai)._safe_private_reply("lol yeah", _goal(command), _message("reinforce me"))
        self.assertEqual(result.final_reply, "lol yeah")
        self.assertEqual(result.rewrite_source, "original")
        self.assertEqual(ai.calls, 0)

    def test_done_gets_validated_llm_rewrite(self) -> None:
        ai = _RewriteAI("give me a sec, I'll check.")
        command = self.parser.parse("reinforce me", sender_uid="p1")
        result = self._loop(ai)._safe_private_reply("done", _goal(command), _message("reinforce me"))
        self.assertEqual(result.final_reply, "give me a sec, I'll check.")
        self.assertEqual(result.rewrite_source, "llm_safe_rewrite")
        self.assertEqual(ai.calls, 1)

    def test_implicit_commitment_and_action_in_progress_are_blocked(self) -> None:
        ai = _RewriteAI("lemme check.")
        command = self.parser.parse("reinforce me", sender_uid="p1")
        loop = self._loop(ai)
        for unsafe in ("Sure thing!", "I'm reinforcing now"):
            result = loop._safe_private_reply(unsafe, _goal(command), _message("reinforce me"))
            self.assertNotIn("reinforc", result.final_reply.casefold())
            self.assertNotEqual(result.rewrite_source, "original")

    def test_future_action_promise_gets_rewritten(self) -> None:
        ai = _RewriteAI("lemme check the rally.")
        command = self.parser.parse("join my rally", sender_uid="p1")
        result = self._loop(ai)._safe_private_reply(
            "Sure, I'll join your rally.", _goal(command), _message("join my rally"),
        )
        self.assertEqual(result.final_reply, "lemme check the rally.")
        self.assertEqual(result.rewrite_source, "llm_safe_rewrite")

    def test_high_risk_attack_on_it_uses_non_committal_reply(self) -> None:
        ai = _RewriteAI("not yet, I'll check.")
        command = self.parser.parse("attack him", sender_uid="p1")
        result = self._loop(ai)._safe_private_reply("on it", _goal(command), _message("attack him"))
        self.assertEqual(result.final_reply, "not yet, I'll check.")
        self.assertEqual(result.rewrite_source, "llm_safe_rewrite")

    def test_unsafe_or_failed_rewrite_uses_deterministic_contextual_fallback(self) -> None:
        command = self.parser.parse("attack him", sender_uid="p1")
        unsafe = self._loop(_RewriteAI("I'm attacking now"))._safe_private_reply(
            "on it", _goal(command), _message("attack him"),
        )
        failed = self._loop(_RewriteAI("", error="offline"))._safe_private_reply(
            "on it", _goal(command), _message("attack him"),
        )
        self.assertEqual(unsafe.rewrite_source, "deterministic_fallback")
        self.assertEqual(failed.rewrite_source, "deterministic_fallback")
        self.assertNotIn("attack", unsafe.final_reply.casefold())
        self.assertEqual(unsafe.final_reply, failed.final_reply)

    def test_executed_action_can_state_completion(self) -> None:
        guard = ActionClaimGuard()
        command = self.parser.parse("reinforce me", sender_uid="p1")
        result = guard.validate("done", command, [{"command_type": "REINFORCE_PLAYER", "status": "EXECUTED"}])
        self.assertFalse(result.changed)

    def test_recorder_and_feedback_keep_original_final_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feedback = FeedbackStore(Path(tmp) / "feedback.db")
            recorder = _Recorder()
            try:
                loop = self._loop(_RewriteAI("unused"), feedback_store=feedback, recorder=recorder)
                loop.config.record_events = True
                command = self.parser.parse("reinforce me", sender_uid="p1")
                loop._record_private_training(
                    payload=_message("reinforce me"), analysis={"intent": "ACTION_REQUEST"},
                    decision="REPLY", reply="lemme check.", command=command,
                    pending_command={"command_type": "REINFORCE_PLAYER", "status": "DETECTED"},
                    reply_original="done", reply_guard_triggered=True,
                    reply_rewritten=True, reply_rewrite_source="llm_safe_rewrite",
                    guard_reason="Blocked an unconfirmed action claim.",
                )
                self.assertEqual(recorder.raw[0]["reply_original"], "done")
                self.assertEqual(recorder.raw[0]["reply_rewrite_source"], "llm_safe_rewrite")
                stored = feedback.list_recent(1)[0]
                self.assertEqual(stored.model_reply, "lemme check.")
                self.assertEqual(stored.original_reply, "done")
                self.assertEqual(stored.rewrite_source, "llm_safe_rewrite")
            finally:
                feedback.close()


if __name__ == "__main__":
    unittest.main()
