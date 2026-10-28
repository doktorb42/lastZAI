"""Polityka kwalifikacji danych Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import unittest

import trening.agency_dataset_policy as legacy
import trening.agency_dataset_policy as training


class AgencyDatasetPolicyMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_policy_functions(self) -> None:
        self.assertIs(legacy.positive_real_eligibility, training.positive_real_eligibility)
        self.assertIs(legacy.is_unsafe_unconfirmed_action_claim, training.is_unsafe_unconfirmed_action_claim)

    def test_unreviewed_or_unsafe_real_message_is_not_positive_training_data(self) -> None:
        unreviewed = training.positive_real_eligibility(
            {"channel": "custom", "room_id": "custom_1_2_v2", "player_uid": "real", "input_message": "help", "reply": "okay"},
            None,
        )
        unsafe = training.positive_real_eligibility(
            {"channel": "custom", "room_id": "custom_1_2_v2", "player_uid": "real", "input_message": "reinforce", "reply": "I sent reinforcements."},
            {"rating": "GOOD"},
        )
        self.assertFalse(unreviewed.eligible)
        self.assertIn("unreviewed_feedback", unreviewed.reasons)
        self.assertFalse(unsafe.eligible)
        self.assertIn("unsafe_unconfirmed_action_claim", unsafe.reasons)


if __name__ == "__main__":
    unittest.main()
