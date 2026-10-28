"""Magazyn decyzji Agency ma jedno źródło w Treningu."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import trening.agency_store as legacy
import trening.agency_store as training


class AgencyStoreMigrationTests(unittest.TestCase):
    def test_legacy_import_exposes_the_same_store(self) -> None:
        self.assertIs(legacy.AgencyStore, training.AgencyStore)

    def test_temp_store_records_review_data_without_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agency.db"
            with training.AgencyStore(path) as store:
                decision_id = store.record_reactive(
                    goal_id=None,
                    player_uid="uid-test",
                    room_id="room-test",
                    incoming_text="hello",
                    decision={"decision": "REPLY", "reason_code": "TEST", "message": "preview"},
                    status="PREVIEW",
                    provenance={"source": "test"},
                )
                store.add_feedback(decision_id, rating="GOOD")
                self.assertEqual(store.get_decision(decision_id)["decision"], "REPLY")
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
