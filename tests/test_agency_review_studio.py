from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from trening.agency_review_studio import ReviewError, ReviewStudio


def reactive(record_id: str, *, family: str = "family-a", message: str = "hey") -> dict:
    return {
        "id": record_id, "source": "synthetic_curated", "synthetic": True,
        "mode": "AGENCY_REACTIVE", "category_group": "natural_social", "category": "hello",
        "scenario_family": family, "context": [], "player_memories": [], "input": "hello",
        "target": {"decision": "REPLY", "reason_code": "CASUAL_GREETING", "message": message},
    }


def proactive(record_id: str) -> dict:
    return {
        "id": record_id, "source": "synthetic_curated", "synthetic": True,
        "mode": "AGENCY_PROACTIVE", "category_group": "proactive_agency", "category": "followup",
        "scenario_family": "family-proactive", "context": [], "player_memories": [], "input": None,
        "candidates": [{"uid": "player-a", "name": "A"}],
        "target": {"decision": "START_CONVERSATION", "reason_code": "CASUAL_GREETING", "message": "hey again", "target_uid": "player-a"},
    }


class ReviewStudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.v0 = root / "v0.jsonl"
        self.v1 = root / "v1.jsonl"
        self.real = root / "real.jsonl"
        self.train = root / "train.jsonl"
        self.validation = root / "validation.jsonl"
        self.store = root / "review.json"
        self.legacy = root / "legacy.json"
        self._write(self.v0, [reactive("a"), proactive("p")])
        self._write(self.v1, [reactive("b", family="family-b", message="hello there")])
        self._write(self.real, [{"id": "real-1", "incoming_text": "hi", "peva_reply": "hello", "source": "real_unreviewed", "synthetic": False}])
        self._write(self.train, [{"id": "a"}, {"id": "p"}])
        self._write(self.validation, [{"id": "b"}])
        self.studio = ReviewStudio(
            sources={"v0": self.v0, "v1": self.v1, "real": self.real}, review_store=self.store,
            legacy_review=self.legacy, pilot_train=self.train, pilot_validation=self.validation,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, rows: list[dict]) -> None:
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_imports_legacy_full_record_note_and_exports_integrity(self) -> None:
        changed = reactive("a", message="edited reply")
        document = {"schema_version": 2, "reviews": [{"id": "a", "source_file": "v0", "review": {"status": "EDIT", "note": json.dumps(changed)}}]}
        state = self.studio.import_document(document)
        self.assertEqual(state["a"]["reviewed_record"]["target"]["message"], "edited reply")
        exported = self.studio.export_document(state)
        reloaded = self.studio.import_document(exported)
        self.assertEqual(reloaded["a"]["reviewed_record"], changed)

    def test_target_only_patch_and_conflicting_id_are_handled_safely(self) -> None:
        state = self.studio.import_document({"reviews": [{"id": "a", "source_file": "v0", "review": {"status": "EDIT", "patch": {"id": "a", "target": {"message": "patched"}}}}]})
        self.assertEqual(state["a"]["reviewed_record"]["target"]["message"], "patched")
        with self.assertRaisesRegex(ReviewError, "conflicts"):
            self.studio.import_document({"reviews": [{"id": "a", "review": {"status": "EDIT", "patch": {"id": "b", "target": {"message": "wrong"}}}}]})

    def test_duplicate_review_ids_and_invalid_reason_codes_are_rejected(self) -> None:
        with self.assertRaisesRegex(ReviewError, "Duplicate review item"):
            self.studio.import_document({"reviews": [{"id": "a", "review": {"status": "GOOD"}}, {"id": "a", "review": {"status": "GOOD"}}]})
        invalid = reactive("a")
        invalid["target"]["reason_code"] = "not valid!"
        issues = self.studio.validate_record(invalid, source_dataset="v0")
        self.assertIn("invalid_decision_or_reason_code", {item["code"] for item in issues})
        unknown = reactive("a")
        unknown["target"]["reason_code"] = "NEW_UNREGISTERED_REASON"
        self.assertIn("unsupported_reason_code", {item["code"] for item in self.studio.validate_record(unknown, source_dataset="v0")})

    def test_proactive_candidate_and_action_confirmation_validation(self) -> None:
        bad = proactive("p")
        bad["target"]["target_uid"] = "invented"
        self.assertIn("invalid_decision_or_reason_code", {item["code"] for item in self.studio.validate_record(bad, source_dataset="v0")})
        claimed = reactive("a", message="I teleported already")
        self.assertIn("unconfirmed_game_action_claim", {item["code"] for item in self.studio.validate_record(claimed, source_dataset="v0")})
        claimed["context"] = ["SYSTEM: Teleport completed and confirmed by the game"]
        self.assertNotIn("unconfirmed_game_action_claim", {item["code"] for item in self.studio.validate_record(claimed, source_dataset="v0")})

    def test_atomic_saves_create_backup_and_split_leakage_is_reported(self) -> None:
        state = self.studio.load_state()
        self.studio.save_state(state)
        original_bytes = self.store.read_bytes()
        self.studio.update_record(state, record_id="a", status="GOOD", note="checked", reviewed_record=state["a"]["reviewed_record"])
        backup = self.studio.save_state(state)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.is_file())
        saved_bytes = self.store.read_bytes()
        with mock.patch("trening.agency_review_studio.os.replace", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                self.studio.save_state(state)
        self.assertNotEqual(saved_bytes, original_bytes)
        self.assertEqual(self.store.read_bytes(), saved_bytes)
        self._write(self.validation, [{"id": "a"}])
        issues = self.studio.analytics(state)["validation"]["errors"]
        self.assertIn("train_validation_leakage", {item["code"] for item in issues})

    def test_real_project_review_file_is_backward_compatible(self) -> None:
        studio = ReviewStudio()
        state = studio.load_state()
        self.assertEqual(len(state), 182)
        self.assertFalse(any(item["severity"] == "error" for item in studio.validate_state(state)))

