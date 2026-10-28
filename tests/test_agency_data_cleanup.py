from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import trening.agency_cleanup as cleanup
from trening.agency_dataset_audit import EXPECTED_COUNTS, audit_dataset
from trening.agency_dataset_policy import positive_real_eligibility
from trening.agency_real_candidate_export import OUTPUT as REAL_CANDIDATES
from trening.agency_seed_dataset import OUTPUT as SYNTHETIC_DATASET
from mozg.conversation_store import ConversationStore


ROOT = Path(__file__).resolve().parents[1]


class AgencyPolicyTests(unittest.TestCase):
    def _record(self, **overrides: object) -> dict[str, object]:
        record: dict[str, object] = {
            "message_key": "safe-key",
            "room_id": "custom_90001_90002_v2",
            "player_uid": "90001",
            "channel": "custom",
            "input_message": "what happened?",
            "reply": "i'm checking",
            "command_status": None,
        }
        record.update(overrides)
        return record

    def test_public_multi_sender_room_is_excluded_from_private_positive_data(self) -> None:
        decision = positive_real_eligibility(
            self._record(room_id="custom_lang_en_769", player_uid="1020134332000769"),
            {"rating": "GOOD"},
        )
        self.assertFalse(decision.eligible)
        self.assertIn("public_or_group_room", decision.reasons)

    def test_manifest_test_marker_is_excluded(self) -> None:
        manifest = {
            "decisions": [{
                "source_message_key": "stage-test", "classification": "TEST_SAFETY",
                "training_usage": ["TEST_SAFETY", "EXCLUDE_POSITIVE"],
            }]
        }
        decision = positive_real_eligibility(
            self._record(message_key="stage-test", input_message="PEVA_V2_STAGE1_TEST"),
            {"rating": "GOOD"}, manifest=manifest,
        )
        self.assertFalse(decision.eligible)
        self.assertIn("cleanup_classification_excluded", decision.reasons)

    def test_null_feedback_is_not_good(self) -> None:
        decision = positive_real_eligibility(self._record(), None)
        self.assertFalse(decision.eligible)
        self.assertIn("unreviewed_feedback", decision.reasons)

    def test_unsafe_action_reply_is_not_positive_even_with_good_rating(self) -> None:
        decision = positive_real_eligibility(
            self._record(reply="I reinforced you.", command_status="DETECTED"),
            {"rating": "GOOD"},
        )
        self.assertFalse(decision.eligible)
        self.assertIn("unsafe_unconfirmed_action_claim", decision.reasons)

    def test_inactive_false_memory_is_not_runtime_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversation.db"
            store = ConversationStore(path)
            try:
                memory_id = store.add_memory(
                    player_uid="90001", memory_type="identity", content="my name is Alex",
                    importance=0.85, confidence=0.98,
                )
                self.assertTrue(store.deactivate_memory(memory_id))
                self.assertEqual(store.get_memory_context(player_uid="90001"), [])
            finally:
                store.close()


class AgencySyntheticDatasetTests(unittest.TestCase):
    def test_exact_synthetic_distribution_and_valid_control_decisions(self) -> None:
        report = audit_dataset(SYNTHETIC_DATASET)
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["count"], 80)
        self.assertEqual(report["category_counts"], EXPECTED_COUNTS)
        self.assertGreaterEqual(report["proactive_decisions"]["START_CONVERSATION"], 5)
        self.assertGreaterEqual(report["proactive_decisions"]["DO_NOTHING"], 5)
        rows = [json.loads(line) for line in SYNTHETIC_DATASET.read_text(encoding="utf-8").splitlines() if line]
        decisions = {row["target"]["decision"] for row in rows}
        self.assertTrue({"IGNORE", "WAIT", "CHANGE_TOPIC", "DO_NOTHING"} <= decisions)
        for row in rows:
            if row["mode"] == "AGENCY_PROACTIVE" and row["target"]["decision"] == "START_CONVERSATION":
                self.assertIn(row["target"]["target_uid"], {candidate["uid"] for candidate in row["candidates"]})

    def test_synthetic_and_real_review_queue_remain_separate(self) -> None:
        synthetic = [json.loads(line) for line in SYNTHETIC_DATASET.read_text(encoding="utf-8").splitlines() if line]
        real = [json.loads(line) for line in REAL_CANDIDATES.read_text(encoding="utf-8").splitlines() if line]
        self.assertTrue(all(row["synthetic"] is True and row["source"] == "synthetic_curated" for row in synthetic))
        self.assertTrue(all(row["synthetic"] is False and row["source"] == "real_unreviewed" for row in real))
        self.assertTrue(all(row["eligible_for_positive_training"] is False for row in real))


class AgencyCleanupIdempotenceTests(unittest.TestCase):
    def test_cleanup_rejects_dummy_p1_and_is_idempotent(self) -> None:
        # These are isolated copies of historical databases.  Do not create
        # the temporary directory inside the live ``data/`` tree: on Windows
        # that directory can be busy while BlueStacks or backup software is
        # reading it, turning a pure test into a stalled filesystem retry.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            databases = root / "databases"
            backup = root / "backup"
            databases.mkdir()
            backup.mkdir()
            checksum_rows = []
            for name in cleanup.DB_NAMES:
                original = ROOT / "data" / "backups" / "pre_agency_cleanup_2026-10-20" / name
                destination = databases / name
                backup_copy = backup / name
                shutil.copy2(original, destination)
                shutil.copy2(original, backup_copy)
                checksum_rows.append({
                    "file": name,
                    "sha256": hashlib.sha256(backup_copy.read_bytes()).hexdigest(),
                    "bytes": backup_copy.stat().st_size,
                })
            (backup / "SHA256SUMS.json").write_text(json.dumps(checksum_rows), encoding="utf-8")
            previous = (cleanup.DATABASES, cleanup.BACKUP_DIR, cleanup.CLEANUP_MANIFEST_PATH)
            cleanup.DATABASES = databases
            cleanup.BACKUP_DIR = backup
            cleanup.CLEANUP_MANIFEST_PATH = root / "cleanup" / "manifest.json"
            try:
                first = cleanup.inspect_and_apply(apply=True)
                second = cleanup.inspect_and_apply(apply=True)
                self.assertEqual(first["summary"]["modifications"]["conversation.player_memory_deactivated"], 1)
                self.assertEqual(first["summary"]["modifications"]["memory_review.rejected"], 37)
                self.assertEqual(second["summary"]["modifications"], {})
                with closing(sqlite3.connect(databases / "conversation.db")) as connection:
                    active = connection.execute(
                        "SELECT active FROM player_memory WHERE player_uid = ? AND content = ?",
                        (cleanup.OG_UID, cleanup.FALSE_MEMORY),
                    ).fetchone()[0]
                    self.assertEqual(active, 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 104)
                with closing(sqlite3.connect(databases / "memory_review.db")) as connection:
                    rejected = connection.execute(
                        "SELECT COUNT(*) FROM memory_review_candidates WHERE player_uid = 'p1' AND status = 'REJECTED'"
                    ).fetchone()[0]
                    self.assertEqual(rejected, 34)
            finally:
                cleanup.DATABASES, cleanup.BACKUP_DIR, cleanup.CLEANUP_MANIFEST_PATH = previous


if __name__ == "__main__":
    unittest.main()
