"""Kontrakty historycznego Agency mają jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_types as legacy
import trening.agency_types as training


class AgencyTypesMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_decision_contract(self) -> None:
        self.assertIs(legacy.AgencyDecision, training.AgencyDecision)
        self.assertIs(legacy.ReactiveDecision, training.ReactiveDecision)
        self.assertIs(legacy.ProactiveDecision, training.ProactiveDecision)

    def test_silent_decisions_cannot_carry_a_message(self) -> None:
        with self.assertRaises(ValueError):
            training.AgencyDecision.parse_reactive(
                {"decision": "WAIT", "reason_code": "NEED_CONTEXT", "message": "hello"}
            )


if __name__ == "__main__":
    unittest.main()
