from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from lastz_model_benchmark.conversation_system.lastz_conversation.contracts import (
    CHAT_PROMPT_VERSION,
    CONTEXT_CONTRACT_VERSION,
    MEMORY_PROMPT_VERSION,
    MEMORY_UPDATE_SCHEMA_VERSION,
    ContractError,
    PrivateChatObservation,
    canonical_json,
    validate_task_chat_input,
    validate_task_memory_input,
)
from lastz_model_benchmark.conversation_system.lastz_conversation.exporter import (
    ExportError,
    export_approved_examples,
)
from lastz_model_benchmark.conversation_system.lastz_conversation.service import ConversationService


REPLY = "yeah bro, I can join after reset"
ROOM_ID = "lastz-room-nova"
COUNTERPART_UID = "lastz-player-nova-42"
PEVA_UID = "lastz-peva-7"


def task_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Extract the exact saved task payload without rebuilding it."""

    assert len(messages) == 2
    prefix, separator, encoded = messages[1]["content"].partition("\n")
    assert prefix.startswith("[PEVA TASK_")
    assert separator
    payload = json.loads(encoded)
    assert isinstance(payload, dict)
    return payload


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ConversationSystemV2Tests(unittest.TestCase):
    """Contract and persistence tests; every SQLite database is temporary."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.service = ConversationService(self.root / "memory.sqlite3")
        self.enrolled = self.enroll(self.service)

    def tearDown(self) -> None:
        self.service.close()
        self.temp.cleanup()

    @staticmethod
    def enroll(service: ConversationService) -> dict[str, str]:
        return service.enroll_verified_player(
            nickname="Nova",
            identity_type="game_player_id",
            identity_value=COUNTERPART_UID,
            language="en",
            alliance_name="Night Owls",
            relationship_style="friendly_casual",
            external_room_id=ROOM_ID,
            channel="private",
            counterpart_game_player_id=COUNTERPART_UID,
            peva_game_player_id=PEVA_UID,
            verification_state="verified",
        )

    def observation(
        self,
        *,
        source_key: str = "message-1",
        text: str = "Can you join after reset?",
        person_index: int = 0,
        room_id: str = ROOM_ID,
        identity_value: str = COUNTERPART_UID,
        peva_uid: str = PEVA_UID,
    ) -> PrivateChatObservation:
        return PrivateChatObservation(
            person_index=person_index,
            identity_type="game_player_id",
            identity_value=identity_value,
            nickname="Nova",
            incoming_text=text,
            source_message_key=source_key,
            source_message_id="external-" + source_key,
            observed_at="2026-10-14T12:00:00Z",
            external_room_id=room_id,
            channel="private",
            peva_game_player_id=peva_uid,
            verification_state="verified",
        )

    def capture(self, **kwargs: Any) -> dict[str, Any]:
        captured = self.service.capture_incoming(self.observation(**kwargs))
        self.assertEqual(captured["status"], "created", captured)
        return captured

    def draft_and_approve(self, turn_id: str, reply: str = REPLY) -> None:
        verdict = self.service.record_model_draft(
            turn_id=turn_id,
            raw_reply=reply,
            model_provenance={"provider": "unit-test"},
        )
        self.assertTrue(verdict["valid"], verdict)
        approved = self.service.approve_reply(turn_id=turn_id, approved_reply=reply, reviewer="tester")
        self.assertTrue(approved["valid"], approved)

    @staticmethod
    def memory_update(
        *,
        value: str = "Often works late shifts and may miss Tyrant.",
        confidence: float = 0.92,
        scope: str = "long_term",
        expires_at: str | None = None,
        evidence: list[str] | None = None,
    ) -> str:
        return json.dumps(
            {
                "schema_version": MEMORY_UPDATE_SCHEMA_VERSION,
                "operations": [
                    {
                        "op": "upsert_memory",
                        "kind": "fact",
                        "key": "schedule.work_shift",
                        "value": value,
                        "scope": scope,
                        "confidence": confidence,
                        "expires_at": expires_at,
                        "evidence_message_refs": evidence or ["m0"],
                    }
                ],
            }
        )

    def apply_memory(self, turn_id: str, raw_output: str) -> dict[str, Any]:
        proposal = self.service.record_memory_output(turn_id=turn_id, raw_output=raw_output)
        self.assertTrue(proposal["valid"], proposal)
        self.service.apply_memory_proposal(attempt_id=str(proposal["attempt_id"]), applied_by="tester")
        return proposal

    def cursor(self, conversation_id: str) -> dict[str, Any]:
        row = self.service.repository.connection.execute(
            "SELECT * FROM conversation_cursors WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        return dict(row)

    def test_verified_external_identity_binding_and_room_routing(self) -> None:
        route = self.service.repository.external_route_for_conversation(self.enrolled["conversation_id"])
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route["external_room_id"], ROOM_ID)
        self.assertEqual(route["counterpart_game_player_id"], COUNTERPART_UID)
        self.assertEqual(route["peva_game_player_id"], PEVA_UID)
        self.assertEqual(route["verification_state"], "verified")
        self.capture()

        wrong_room = self.service.capture_incoming(
            self.observation(source_key="wrong-room", room_id="different-lastz-room")
        )
        self.assertEqual(wrong_room["status"], "quarantined")
        self.assertEqual(wrong_room["reason"], "unbound_external_route")
        wrong_counterpart = self.service.capture_incoming(
            self.observation(source_key="wrong-player", identity_value="different-lastz-player")
        )
        self.assertEqual(wrong_counterpart["status"], "quarantined")

    def test_duplicate_ingest_is_idempotent_and_audited(self) -> None:
        first = self.capture()
        second = self.service.capture_incoming(self.observation())
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["message_id"], first["message_id"])
        self.assertEqual(second["turn_id"], first["turn_id"])
        revisions = self.service.repository.connection.execute(
            "SELECT outcome FROM message_ingest_revisions WHERE message_id=? ORDER BY rowid", (first["message_id"],)
        ).fetchall()
        self.assertEqual([row["outcome"] for row in revisions], ["created", "duplicate"])

    def test_alias_context_has_no_routing_or_sqlite_identifier_leakage(self) -> None:
        captured = self.capture()
        turn = self.service.repository.get_turn(str(captured["turn_id"]))
        context = turn["context_json"]
        self.assertEqual(context["conversation_ref"], "c0")
        self.assertEqual(context["player"]["player_ref"], "p0")
        self.assertEqual(context["incoming_message"]["message_ref"], "m0")
        self.assertEqual(context["incoming_message"]["direction"], "incoming")
        payload = task_payload(self.service.draft_prompt(str(captured["turn_id"])))
        self.assertEqual(payload["context"], context)
        self.assertEqual(validate_task_chat_input(payload), payload)
        serialized = json_text(payload)
        for secret in (
            ROOM_ID,
            COUNTERPART_UID,
            PEVA_UID,
            str(captured["player_id"]),
            str(captured["conversation_id"]),
            str(captured["message_id"]),
            str(captured["turn_id"]),
        ):
            self.assertNotIn(secret, serialized)
        for forbidden_field in ("room_id", "external_room_id", "player_id", "message_id", "source_message_key"):
            self.assertNotIn(f'"{forbidden_field}"', serialized)
        malformed = copy.deepcopy(payload)
        malformed["context"]["room_id"] = ROOM_ID
        with self.assertRaises(ContractError):
            validate_task_chat_input(malformed)

    def test_snapshot_aliases_are_deterministic_across_fresh_databases(self) -> None:
        def snapshot_for(path: Path) -> dict[str, Any]:
            service = ConversationService(path)
            try:
                self.enroll(service)
                captured = service.capture_incoming(self.observation())
                return service.repository.get_turn(str(captured["turn_id"]))["context_json"]
            finally:
                service.close()

        left = snapshot_for(self.root / "deterministic-left.sqlite3")
        right = snapshot_for(self.root / "deterministic-right.sqlite3")
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(left["incoming_message"]["message_ref"], "m0")
        self.assertEqual(left["conversation_ref"], "c0")
        self.assertEqual(left["player"]["player_ref"], "p0")

    def test_chat_and_memory_share_the_same_exact_immutable_context_contract(self) -> None:
        captured = self.capture()
        turn_id = str(captured["turn_id"])
        context_before_review = self.service.repository.get_turn(turn_id)["context_json"]
        chat_input = task_payload(self.service.draft_prompt(turn_id))
        self.assertEqual(chat_input["prompt_version"], CHAT_PROMPT_VERSION)
        self.assertEqual(chat_input["context_contract_version"], CONTEXT_CONTRACT_VERSION)
        self.assertEqual(chat_input["context"], context_before_review)
        self.draft_and_approve(turn_id)
        memory_input = task_payload(self.service.memory_prompt(turn_id))
        self.assertEqual(memory_input["prompt_version"], MEMORY_PROMPT_VERSION)
        self.assertEqual(memory_input["context_contract_version"], CONTEXT_CONTRACT_VERSION)
        self.assertEqual(canonical_json(memory_input["context"]), canonical_json(chat_input["context"]))
        self.assertEqual(validate_task_memory_input(memory_input), memory_input)
        self.assertEqual(
            memory_input["allowed_refs"],
            {"message_refs": ["m0"], "memory_refs": [], "thread_refs": [], "promise_refs": []},
        )
        chat_unknown = copy.deepcopy(chat_input)
        chat_unknown["unexpected"] = True
        with self.assertRaises(ContractError):
            validate_task_chat_input(chat_unknown)
        memory_unknown = copy.deepcopy(memory_input)
        memory_unknown["unexpected"] = True
        with self.assertRaises(ContractError):
            validate_task_memory_input(memory_unknown)

    def test_message_lifecycle_and_cursors_are_separate(self) -> None:
        captured = self.capture()
        turn_id = str(captured["turn_id"])
        conversation_id = str(captured["conversation_id"])
        lifecycle = self.service.repository.lifecycle_for_turn(turn_id)
        self.assertIsNotNone(lifecycle["ingested_at"])
        self.assertIsNone(lifecycle["processed_at"])
        self.assertIsNone(lifecycle["reply_generated_at"])
        self.assertIsNone(lifecycle["approved_at"])
        self.assertIsNone(lifecycle["delivered_at"])
        cursor = self.cursor(conversation_id)
        self.assertEqual(cursor["last_ingested_message_id"], captured["message_id"])
        self.assertIsNone(cursor["last_processed_message_id"])
        self.assertIsNone(cursor["last_reply_generated_message_id"])
        self.assertIsNone(cursor["last_approved_message_id"])
        self.assertIsNone(cursor["last_delivered_message_id"])
        self.draft_and_approve(turn_id)
        cursor = self.cursor(conversation_id)
        self.assertEqual(cursor["last_reply_generated_message_id"], captured["message_id"])
        self.assertEqual(cursor["last_approved_message_id"], captured["message_id"])
        self.assertIsNone(cursor["last_processed_message_id"])
        self.apply_memory(
            turn_id,
            json.dumps(
                {
                    "schema_version": MEMORY_UPDATE_SCHEMA_VERSION,
                    "operations": [{"op": "noop", "reason": "No durable update is needed."}],
                }
            ),
        )
        cursor = self.cursor(conversation_id)
        self.assertEqual(cursor["last_processed_message_id"], captured["message_id"])
        self.assertIsNone(cursor["last_delivered_message_id"])
        delivered = self.service.record_delivery_confirmation(
            turn_id=turn_id,
            delivery_key="delivery-1",
            delivered_at="2026-10-14T12:01:00Z",
        )
        self.assertEqual(delivered["status"], "delivered")
        cursor = self.cursor(conversation_id)
        self.assertEqual(cursor["last_delivered_message_id"], captured["message_id"])
        lifecycle = self.service.repository.lifecycle_for_turn(turn_id)
        self.assertTrue(
            all(
                lifecycle[field] is not None
                for field in ("ingested_at", "reply_generated_at", "approved_at", "processed_at", "delivered_at")
            )
        )
        self.assertEqual(
            [event["state"] for event in lifecycle["events"]],
            ["ingested", "reply_generated", "approved", "processed", "delivered"],
        )

    def test_memory_output_rejects_unknown_fields_sql_and_arbitrary_actions(self) -> None:
        captured = self.capture()
        turn_id = str(captured["turn_id"])
        self.draft_and_approve(turn_id)
        unknown_field = json.dumps(
            {
                "schema_version": MEMORY_UPDATE_SCHEMA_VERSION,
                "operations": [
                    {
                        "op": "upsert_memory",
                        "kind": "fact",
                        "key": "schedule.work_shift",
                        "value": "Often works late shifts.",
                        "scope": "long_term",
                        "confidence": 0.9,
                        "expires_at": None,
                        "evidence_message_refs": ["m0"],
                        "sql": "DROP TABLE players",
                    }
                ],
            }
        )
        rejected = self.service.record_memory_output(turn_id=turn_id, raw_output=unknown_field)
        self.assertFalse(rejected["valid"])
        self.assertIn("unsupported fields", rejected["errors"][0])
        arbitrary_action = json.dumps(
            {"schema_version": MEMORY_UPDATE_SCHEMA_VERSION, "operations": [{"op": "send_message", "text": "hi"}]}
        )
        rejected = self.service.record_memory_output(turn_id=turn_id, raw_output=arbitrary_action)
        self.assertFalse(rejected["valid"])
        self.assertIn("unsupported op", rejected["errors"][0])
        sql_in_text = json.dumps(
            {"schema_version": MEMORY_UPDATE_SCHEMA_VERSION, "operations": [{"op": "noop", "reason": "DROP TABLE players"}]}
        )
        rejected = self.service.record_memory_output(turn_id=turn_id, raw_output=sql_in_text)
        self.assertFalse(rejected["valid"])
        self.assertIn("must not contain SQL", rejected["errors"][0])
        runtime_identifier = self.service.record_memory_output(
            turn_id=turn_id,
            raw_output=self.memory_update(value=f"Store the room identifier {ROOM_ID}."),
        )
        self.assertFalse(runtime_identifier["valid"])
        self.assertIn("forbidden runtime identifier", runtime_identifier["errors"][0])

    def test_invalid_evidence_and_temporary_expiry_are_rejected(self) -> None:
        captured = self.capture()
        turn_id = str(captured["turn_id"])
        self.draft_and_approve(turn_id)
        invalid_evidence = self.service.record_memory_output(turn_id=turn_id, raw_output=self.memory_update(evidence=["m999"]))
        self.assertFalse(invalid_evidence["valid"])
        self.assertIn("allowed_refs", invalid_evidence["errors"][0])
        missing_expiry = self.service.record_memory_output(
            turn_id=turn_id, raw_output=self.memory_update(scope="temporary", expires_at=None)
        )
        self.assertFalse(missing_expiry["valid"])
        self.assertIn("temporary memory requires expires_at", missing_expiry["errors"][0])
        past_expiry = self.service.record_memory_output(
            turn_id=turn_id, raw_output=self.memory_update(scope="temporary", expires_at="2020-01-01T00:00:00Z")
        )
        self.assertFalse(past_expiry["valid"])
        self.assertIn("expires_at must be in the future", past_expiry["errors"][0])
        self.apply_memory(turn_id, self.memory_update(scope="temporary", expires_at="2027-10-14T12:00:00Z"))
        next_capture = self.capture(source_key="message-2", text="thanks, that helps")
        next_context = self.service.repository.get_turn(str(next_capture["turn_id"]))["context_json"]
        self.assertEqual(next_context["temporary_memory"][0]["memory_ref"], "mem0")
        self.assertEqual(next_context["temporary_memory"][0]["expires_at"], "2027-10-14T12:00:00Z")

    def test_memory_replacements_preserve_revision_history(self) -> None:
        first = self.capture(text="I work late shifts so I usually miss Tyrant")
        first_turn = str(first["turn_id"])
        self.draft_and_approve(first_turn)
        self.apply_memory(first_turn, self.memory_update(confidence=0.80))
        second = self.capture(
            source_key="message-2", text="My late shifts are still the reason I miss Tyrant most days"
        )
        second_turn = str(second["turn_id"])
        second_context = self.service.repository.get_turn(second_turn)["context_json"]
        self.assertEqual(second_context["long_term_memory"][0]["memory_ref"], "mem0")
        self.draft_and_approve(second_turn)
        self.apply_memory(
            second_turn,
            self.memory_update(value="Usually works late shifts and may miss Tyrant.", confidence=0.95),
        )
        revisions = self.service.repository.connection.execute("SELECT * FROM memory_revisions").fetchall()
        self.assertEqual(len(revisions), 1)
        current = self.service.repository.connection.execute(
            "SELECT value_text, confidence FROM memory_items WHERE memory_key='schedule.work_shift' AND status='active'"
        ).fetchone()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current["value_text"], "Usually works late shifts and may miss Tyrant.")
        self.assertEqual(float(current["confidence"]), 0.95)

    def test_player_ready_gate_normalizes_ascii_apostrophes_and_rejects_reasoning(self) -> None:
        captured = self.capture()
        turn_id = str(captured["turn_id"])
        curly_reply = "I\u2019m happy to join after reset. What time works for you?"
        verdict = self.service.record_model_draft(
            turn_id=turn_id,
            raw_reply=curly_reply,
            model_provenance={"provider": "unit-test"},
        )
        self.assertTrue(verdict["valid"], verdict)
        self.assertEqual(verdict["normalized_text"], "I'm happy to join after reset. What time works for you?")
        self.service.approve_reply(turn_id=turn_id, approved_reply=curly_reply, reviewer="tester")
        memory_input = task_payload(self.service.memory_prompt(turn_id))
        self.assertNotIn("\u2019", memory_input["approved_reply"])
        self.assertIn("'", memory_input["approved_reply"])
        reasoning = self.capture(source_key="reasoning-message", text="hello")
        rejected = self.service.record_model_draft(
            turn_id=str(reasoning["turn_id"]),
            raw_reply="Okay, the user wants a reply. I need to plan a friendly answer.",
            model_provenance={"provider": "unit-test"},
        )
        self.assertFalse(rejected["valid"])
        self.assertIn("visible_meta_reasoning_or_prompt_analysis", rejected["errors"])
        leaky = self.capture(source_key="identifier-message", text="hello again")
        leaky_verdict = self.service.record_model_draft(
            turn_id=str(leaky["turn_id"]),
            raw_reply=f"hey, use {ROOM_ID}",
            model_provenance={"provider": "unit-test"},
        )
        self.assertFalse(leaky_verdict["valid"])
        self.assertIn("forbidden_runtime_identifier_in_reply", leaky_verdict["errors"])

    def test_snapshot_stays_immutable_after_memory_mutation(self) -> None:
        captured = self.capture(text="I work late shifts so I usually miss Tyrant")
        turn_id = str(captured["turn_id"])
        before = copy.deepcopy(self.service.repository.get_turn(turn_id)["context_json"])
        self.draft_and_approve(turn_id)
        self.apply_memory(turn_id, self.memory_update())
        after = self.service.repository.get_turn(turn_id)["context_json"]
        self.assertEqual(canonical_json(before), canonical_json(after))
        self.assertEqual(after["long_term_memory"], [])
        task = self.service.repository.ensure_memory_task_snapshot(turn_id)
        self.assertEqual(canonical_json(task["input_json"]["context"]), canonical_json(before))

    def test_review_export_is_alias_only_and_not_a_training_dataset(self) -> None:
        captured = self.capture()
        turn_id = str(captured["turn_id"])
        original_draft = "yeah bro, I can join after reset"
        approved_edit = "yeah, I can join after reset. What time works for you?"
        verdict = self.service.record_model_draft(
            turn_id=turn_id,
            raw_reply=original_draft,
            model_provenance={"provider": "unit-test"},
        )
        self.assertTrue(verdict["valid"], verdict)
        approved = self.service.approve_reply(turn_id=turn_id, approved_reply=approved_edit, reviewer="tester")
        self.assertTrue(approved["valid"], approved)
        self.apply_memory(
            turn_id,
            json.dumps(
                {
                    "schema_version": MEMORY_UPDATE_SCHEMA_VERSION,
                    "operations": [{"op": "noop", "reason": "No durable update is needed."}],
                }
            ),
        )
        self.service.record_delivery_confirmation(
            turn_id=turn_id,
            delivery_key="delivery-export",
            delivered_at="2026-10-14T12:01:00Z",
        )
        manifest = export_approved_examples(self.service.repository, self.root / "review-exports")
        self.assertEqual(manifest["chat_review_records"], 1)
        self.assertEqual(manifest["memory_review_records"], 1)
        self.assertIn("not_training_data", manifest["status"])
        chat_path = Path(str(manifest["chat_review_records_path"]))
        memory_path = Path(str(manifest["memory_review_records_path"]))
        self.assertTrue(chat_path.exists())
        self.assertTrue(memory_path.exists())
        chat_record = json.loads(chat_path.read_text(encoding="utf-8").strip())
        memory_record = json.loads(memory_path.read_text(encoding="utf-8").strip())
        self.assertEqual(chat_record["training_status"], "review_only_not_a_training_dataset")
        self.assertEqual(memory_record["training_status"], "review_only_not_a_training_dataset")
        self.assertEqual(chat_record["model_draft"], original_draft)
        self.assertEqual(chat_record["approved_target"], {"role": "assistant", "content": approved_edit})
        self.assertEqual(
            canonical_json(chat_record["immutable_snapshot"]["context"]),
            canonical_json(memory_record["immutable_snapshot"]["context"]),
        )
        for record in (chat_record, memory_record):
            serialized = json_text(record)
            for secret in (
                ROOM_ID,
                COUNTERPART_UID,
                PEVA_UID,
                str(captured["player_id"]),
                str(captured["conversation_id"]),
                str(captured["message_id"]),
                str(captured["turn_id"]),
            ):
                self.assertNotIn(secret, serialized)

    def test_export_refuses_unreviewed_and_unrouted_pilot_turns(self) -> None:
        self.capture()
        with self.assertRaises(ExportError):
            export_approved_examples(self.service.repository, self.root / "empty-review-exports")
        legacy_service = ConversationService(self.root / "legacy.sqlite3")
        try:
            legacy_service.enroll_verified_player(
                nickname="Legacy",
                identity_type="game_player_id",
                identity_value="legacy-player",
                language="en",
            )
            legacy_capture = legacy_service.capture_incoming(
                PrivateChatObservation(
                    person_index=0,
                    identity_type="game_player_id",
                    identity_value="legacy-player",
                    nickname="Legacy",
                    incoming_text="hello",
                    source_message_key="legacy-message",
                    observed_at="2026-10-14T12:00:00Z",
                )
            )
            legacy_turn = str(legacy_capture["turn_id"])
            verdict = legacy_service.record_model_draft(
                turn_id=legacy_turn,
                raw_reply="hey bro",
                model_provenance={"provider": "unit-test"},
            )
            self.assertTrue(verdict["valid"], verdict)
            legacy_service.approve_reply(turn_id=legacy_turn, approved_reply="hey bro", reviewer="tester")
            proposal = legacy_service.record_memory_output(
                turn_id=legacy_turn,
                raw_output=json.dumps(
                    {
                        "schema_version": MEMORY_UPDATE_SCHEMA_VERSION,
                        "operations": [{"op": "noop", "reason": "No durable update is needed."}],
                    }
                ),
            )
            self.assertTrue(proposal["valid"], proposal)
            legacy_service.apply_memory_proposal(attempt_id=str(proposal["attempt_id"]), applied_by="tester")
            legacy_service.record_delivery_confirmation(
                turn_id=legacy_turn,
                delivery_key="legacy-delivery",
                delivered_at="2026-10-14T12:01:00Z",
            )
            self.assertEqual(legacy_service.exportable_turns(), [])
        finally:
            legacy_service.close()


if __name__ == "__main__":
    unittest.main()
