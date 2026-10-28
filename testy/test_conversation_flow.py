from __future__ import annotations

import unittest

from mozg.conversation_flow import ConversationFlow


class ConversationFlowTests(unittest.TestCase):
    def test_same_visible_turn_is_never_processed_twice(self) -> None:
        flow = ConversationFlow()
        history = ({"role": "assistant", "content": "hey"},)

        first = flow.permit(
            conversation_header="DOKTORB",
            incoming_text="are you here?",
            history=history,
            now=10.0,
        )
        self.assertTrue(first.allowed)
        flow.complete_turn(
            conversation_header="DOKTORB",
            incoming_text="are you here?",
            history=history,
            now=10.1,
        )
        repeated = flow.permit(
            conversation_header="DOKTORB",
            incoming_text="are you here?",
            history=history,
            now=12.0,
        )

        self.assertFalse(repeated.allowed)
        self.assertEqual(repeated.reason, "same_visible_turn_already_handled")

    def test_new_context_may_be_processed_during_cooldown(self) -> None:
        flow = ConversationFlow()
        first_history = ({"role": "user", "content": "hello"},)
        flow.complete_turn(
            conversation_header="DOKTORB",
            incoming_text="can you help?",
            history=first_history,
            now=10.0,
        )

        next_turn = flow.permit(
            conversation_header="DOKTORB",
            incoming_text="never mind, got it",
            history=(*first_history, {"role": "assistant", "content": "what happened?"}),
            now=11.0,
        )

        self.assertTrue(next_turn.allowed)

    def test_temporary_model_failure_can_be_deferred_without_fingerprinting_turn(self) -> None:
        flow = ConversationFlow()
        flow.defer("local_model_not_ready")

        decision = flow.permit(
            conversation_header="DOKTORB",
            incoming_text="hello",
            history=(),
            now=10.0,
        )

        self.assertTrue(decision.allowed)
