"""Stan dostarczenia Rąk ma jedno źródło prawdy po migracji."""

from __future__ import annotations

import unittest

import rece.delivery_state as legacy
import rece.delivery_state as hands


class DeliveryStateMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_delivery_state_type(self) -> None:
        self.assertIs(legacy.DeliveryState, hands.DeliveryState)
        self.assertIs(legacy.delivery_metadata, hands.delivery_metadata)

    def test_incomplete_metadata_stays_fail_closed(self) -> None:
        values = hands.delivery_metadata({"delivery_state": "unknown", "send_tap_attempted": True})
        self.assertEqual(values["delivery_state"], hands.DeliveryState.NOT_ATTEMPTED.value)
        self.assertFalse(hands.is_ambiguous_delivery(values))


if __name__ == "__main__":
    unittest.main()
