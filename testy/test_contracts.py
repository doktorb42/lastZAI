"""Testy fundamentu architektury PEva, niezależne od gry i modelu."""

from __future__ import annotations

import unittest

from contracts import Action, ActionKind, Decision, DecisionKind, VisionResult
from rece import SafetyGate


class ContractTests(unittest.TestCase):
    def test_vision_result_is_source_independent(self) -> None:
        result = VisionResult(screen="PRIVATE_CONVERSATION", confidence=0.97)
        self.assertEqual(result.screen, "PRIVATE_CONVERSATION")

    def test_wait_cannot_contain_an_outgoing_message(self) -> None:
        with self.assertRaises(ValueError):
            Decision(DecisionKind.WAIT, "player is still typing", text="hello")

    def test_safe_blocks_real_actions_until_resume(self) -> None:
        gate = SafetyGate()
        action = Action(ActionKind.SEND_MESSAGE, target="verified_chat", text="hello")
        self.assertFalse(gate.allows(action))
        gate.resume()
        self.assertTrue(gate.allows(action))
        gate.stop()
        self.assertFalse(gate.allows(action))


if __name__ == "__main__":
    unittest.main()
