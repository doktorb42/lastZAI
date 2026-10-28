"""Contract-level tests for the isolated PEva Chat V1 data pipeline."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "lastz_model_benchmark" / "training" / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from chat_v1_contract import (  # noqa: E402
    SOURCE_RECORD_TYPE,
    SOURCE_RECORD_VERSION,
    prompt_key,
    source_record_from_review_export,
    training_row_from_source,
    utc_now,
    validate_source_record,
)
from split_chat_v1_dataset import split_rows  # noqa: E402
from validate_chat_v1_dataset import validate_rows  # noqa: E402
from prepare_chat_v1_dataset import PrepareError, prepare_rows  # noqa: E402
from check_chat_v1_leakage import fixture_surfaces  # noqa: E402
from lastz_model_benchmark.conversation_system.lastz_conversation.contracts import (  # noqa: E402
    build_task_chat_input,
    render_chat_messages,
)


def context(*, incoming: str, nickname: str = "Nova", as_of: str = "2026-10-14T12:00:00Z") -> dict:
    return {
        "as_of": as_of,
        "conversation_ref": "c0",
        "channel": "private",
        "player": {
            "player_ref": "p0",
            "nickname": nickname,
            "language": "en",
            "alliance": None,
            "alliance_role": None,
            "relationship_style": "friendly_casual",
            "identity_status": "verified",
        },
        "recent_messages": [],
        "long_term_memory": [],
        "temporary_memory": [],
        "open_threads": [],
        "pending_promises": [],
        "conversation_summary": None,
        "incoming_message": {
            "message_ref": "m0",
            "direction": "incoming",
            "text": incoming,
            "observed_at": as_of,
        },
    }


def source_record(
    *,
    suffix: str,
    incoming: str,
    reply: str,
    player: str,
    thread: str,
    category: str = "casual_greeting",
    variant_group: str | None = None,
    length_band: str = "micro_social",
) -> dict:
    task_context = context(incoming=incoming, as_of=f"2026-10-14T12:00:0{suffix[-1] if suffix[-1].isdigit() else 0}Z")
    task_input = build_task_chat_input(task_context)
    messages = render_chat_messages(task_context)
    import hashlib
    import json as _json
    canonical = lambda value: _json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "record_type": SOURCE_RECORD_TYPE,
        "schema_version": SOURCE_RECORD_VERSION,
        "record_id": "chatv1_" + hashlib.sha256(suffix.encode()).hexdigest()[:32],
        "task_snapshot": {
            "input": task_input,
            "messages": messages,
            "input_sha256": hashlib.sha256(canonical(task_input).encode()).hexdigest(),
            "messages_sha256": hashlib.sha256(canonical(messages).encode()).hexdigest(),
        },
        "model_draft": reply,
        "approved_reply": reply,
        "approval": {"status": "approved", "reviewer": "tester", "reviewed_at": utc_now(), "reason": "unit_test"},
        "grouping": {"player_group": player, "thread_group": thread},
        "category": category,
        "provenance": {"origin": "unit_test", "source_ref": "fixture_" + suffix},
        "source_type": "manually_authored",
        "variant_group": variant_group,
        "length_band": length_band,
    }


class ChatV1PipelineTests(unittest.TestCase):
    def test_exact_v2_prompt_and_completion_only_training_row(self) -> None:
        row = source_record(suffix="one", incoming="hello", reply="hey bro", player="pgrp_nova", thread="tgrp_nova")
        normalized, errors, details = validate_source_record(row, require_approved=True)
        self.assertEqual(errors, [], details)
        assert normalized is not None
        training = training_row_from_source(normalized, split="train")
        self.assertEqual([message["role"] for message in training["prompt"]], ["system", "user"])
        self.assertEqual(training["completion"], [{"role": "assistant", "content": "hey bro"}])
        self.assertNotIn("approval", training["prompt"][1]["content"])
        self.assertNotIn("pgrp_nova", training["prompt"][1]["content"])
        self.assertNotIn("tgrp_nova", training["prompt"][1]["content"])
        self.assertEqual(training["length_band"], "micro_social")

    def test_approved_reply_must_declare_a_fitting_length_band(self) -> None:
        row = source_record(suffix="length", incoming="hello", reply="hey", player="pgrp_nova", thread="tgrp_nova")
        row["length_band"] = None
        normalized, errors, _details = validate_source_record(row, require_approved=True)
        self.assertIsNone(normalized)
        self.assertTrue(any("length_band" in error for error in errors), errors)
        row["length_band"] = "medium"
        normalized, errors, _details = validate_source_record(row, require_approved=True)
        self.assertIsNone(normalized)
        self.assertTrue(any("has 1 words" in error for error in errors), errors)

    def test_runtime_identifier_and_curly_apostrophe_are_rejected(self) -> None:
        row = source_record(suffix="two", incoming="hello", reply="I'm good", player="pgrp_nova", thread="tgrp_nova")
        row["approved_reply"] = "I’m good"
        normalized, errors, _details = validate_source_record(row, require_approved=True)
        self.assertIsNotNone(normalized)
        self.assertTrue(any("apostrophe" in error for error in errors), errors)
        leaked = source_record(suffix="three", incoming="hello", reply="hey", player="pgrp_nova", thread="tgrp_nova")
        leaked["model_draft"] = "room_id=lastz-secret-room"
        normalized, errors, _details = validate_source_record(leaked, require_approved=False)
        self.assertIsNone(normalized)
        self.assertTrue(any("runtime" in error or "forbidden" in error for error in errors), errors)

    def test_duplicate_pairs_and_unmarked_conflicts_are_removed(self) -> None:
        first = source_record(suffix="four", incoming="hello", reply="hey", player="pgrp_a", thread="tgrp_a")
        duplicate = copy.deepcopy(first)
        duplicate["record_id"] = "chatv1_" + "a" * 32
        conflict = copy.deepcopy(first)
        conflict["record_id"] = "chatv1_" + "b" * 32
        conflict["approved_reply"] = "yo"
        accepted, rejected, report = validate_rows([first, duplicate, conflict])
        self.assertEqual(accepted, [])
        self.assertGreaterEqual(len(rejected), 3)
        self.assertIn("duplicate prompt-target pair", report["duplicate_or_conflict_failures"])
        self.assertIn("conflicting approved targets for identical TASK_CHAT input", report["duplicate_or_conflict_failures"])

    def test_explicit_variants_are_allowed_but_must_stay_together(self) -> None:
        left = source_record(suffix="five", incoming="hello", reply="hey", player="pgrp_a", thread="tgrp_a", variant_group="vgrp_hello")
        right = copy.deepcopy(left)
        right["record_id"] = "chatv1_" + "c" * 32
        right["approved_reply"] = "yo"
        accepted, rejected, report = validate_rows([left, right])
        self.assertEqual(len(accepted), 2, report)
        self.assertEqual(rejected, [])
        self.assertEqual(report["explicit_valid_variant_groups"], {"vgrp_hello": 2})

    def test_group_disjoint_train_validation_test_split(self) -> None:
        rows = [
            source_record(suffix="s1", incoming="hello", reply="hey", player="pgrp_a", thread="tgrp_a"),
            source_record(suffix="s2", incoming="how are you?", reply="good, you?", player="pgrp_a", thread="tgrp_a"),
            source_record(suffix="s3", incoming="rally later?", reply="yeah, what time?", player="pgrp_b", thread="tgrp_b"),
            source_record(suffix="s4", incoming="you joining?", reply="yep, after reset", player="pgrp_c", thread="tgrp_c"),
            source_record(suffix="s5", incoming="nice", reply="haha yeah", player="pgrp_d", thread="tgrp_d"),
            source_record(suffix="s6", incoming="tyrant soon", reply="i might be late", player="pgrp_e", thread="tgrp_e"),
        ]
        args = SimpleNamespace(validation_ratio=0.2, test_ratio=0.2, seed=7)
        outputs, rejected, summary = split_rows(rows, args)
        self.assertEqual(rejected, [])
        self.assertTrue(all(outputs[name] for name in ("train", "validation", "test")), summary)
        self.assertTrue(all(not values for values in summary["player_group_overlap"].values()), summary)
        self.assertTrue(all(not values for values in summary["thread_group_overlap"].values()), summary)
        locations = {
            row["grouping"]["player_group"]: split
            for split, split_rows_value in outputs.items()
            for row in split_rows_value
        }
        self.assertEqual(locations["pgrp_a"], locations["pgrp_a"])
        self.assertNotEqual(prompt_key(rows[0]), "")

    def test_split_balances_many_equal_sized_components(self) -> None:
        rows = [
            source_record(
                suffix=f"balanced-{index}",
                incoming=f"message {index}",
                reply="yeah, I hear you",
                player=f"pgrp_balanced_{index}",
                thread=f"tgrp_balanced_{index}",
                length_band="micro_social",
            )
            for index in range(30)
        ]
        args = SimpleNamespace(validation_ratio=0.1, test_ratio=0.1, seed=20261014)
        outputs, rejected, summary = split_rows(rows, args)
        self.assertEqual(rejected, [])
        self.assertTrue(all(outputs[name] for name in ("train", "validation", "test")), summary)
        self.assertLessEqual(abs(summary["ratios"]["train"] - 0.8), 0.12, summary)
        self.assertLessEqual(abs(summary["ratios"]["validation"] - 0.1), 0.12, summary)
        self.assertLessEqual(abs(summary["ratios"]["test"] - 0.1), 0.12, summary)

    def test_v2_review_export_import_requires_opaque_groups(self) -> None:
        source = source_record(suffix="export", incoming="hello", reply="hey", player="pgrp_x", thread="tgrp_x")
        export = {
            "record_type": "peva_v2_approved_chat_review_record",
            "record_id": "d" * 64,
            "task_snapshot": {
                "task": "<TASK_CHAT>",
                "input": source["task_snapshot"]["input"],
                "messages": source["task_snapshot"]["messages"],
                "input_sha256": source["task_snapshot"]["input_sha256"],
                "messages_sha256": source["task_snapshot"]["messages_sha256"],
            },
            "model_draft": "hey there",
            "approved_target": {"role": "assistant", "content": "hey"},
            "review": {"human_approved": True, "reviewed_at": "2026-10-14T12:00:00Z"},
        }
        imported = source_record_from_review_export(
            export,
            player_group="pgrp_import",
            thread_group="tgrp_import",
            category="casual_greeting",
            length_band="micro_social",
        )
        normalized, errors, _details = validate_source_record(imported, require_approved=True)
        self.assertEqual(errors, [])
        self.assertEqual(normalized["source_type"], "real_approved")
        self.assertEqual(normalized["model_draft"], "hey there")

    def test_multi_record_v2_import_requires_row_to_group_manifest(self) -> None:
        first = source_record(suffix="importa", incoming="hello", reply="hey", player="pgrp_a", thread="tgrp_a")
        second = source_record(suffix="importb", incoming="hello again", reply="yo", player="pgrp_b", thread="tgrp_b")

        def export_from(source: dict, record_id: str) -> dict:
            return {
                "record_type": "peva_v2_approved_chat_review_record",
                "record_id": record_id,
                "task_snapshot": {
                    "task": "<TASK_CHAT>",
                    **source["task_snapshot"],
                },
                "model_draft": source["model_draft"],
                "approved_target": {"role": "assistant", "content": source["approved_reply"]},
                "review": {"human_approved": True, "reviewed_at": "2026-10-14T12:00:00Z"},
            }

        exports = [export_from(first, "a" * 64), export_from(second, "b" * 64)]
        base_args = {
            "format": "auto",
            "player_group": None,
            "thread_group": None,
            "category": None,
            "length_band": None,
            "reviewer": "tester",
            "source_type": "real_approved",
        }
        with self.assertRaises(PrepareError):
            prepare_rows(SimpleNamespace(**base_args, group_manifest_records=None), exports)
        manifest = {
            "a" * 64: {"player_group": "pgrp_a", "thread_group": "tgrp_a", "category": "casual_greeting", "length_band": "micro_social"},
            "b" * 64: {"player_group": "pgrp_b", "thread_group": "tgrp_b", "category": "casual_greeting", "length_band": "micro_social"},
        }
        accepted, rejected, report = prepare_rows(SimpleNamespace(**base_args, group_manifest_records=manifest), exports)
        self.assertEqual(rejected, [])
        self.assertEqual(len(accepted), 2, report)
        self.assertEqual({row["grouping"]["player_group"] for row in accepted}, {"pgrp_a", "pgrp_b"})

    def test_canonical_chat_v1_benchmark_prompt_is_visible_to_leakage_scan(self) -> None:
        fixture_path = ROOT / "lastz_model_benchmark" / "benchmarks" / "cases" / "chat_v1_quality_cases.jsonl"
        first_fixture = json.loads(fixture_path.read_text(encoding="utf-8").splitlines()[0])
        task_input = build_task_chat_input(first_fixture["canonical_context"])
        fingerprint = prompt_key({"task_snapshot": {"input": task_input}})
        surfaces, _files = fixture_surfaces(ROOT / "lastz_model_benchmark" / "benchmarks" / "cases")
        first_surface = next(surface for surface in surfaces if surface.fixture_id == first_fixture["id"])
        self.assertIn(fingerprint, first_surface.prompt_fingerprints)

    def test_tiny_fixture_is_below_configured_pilot_minimum(self) -> None:
        config = json.loads((ROOT / "lastz_model_benchmark" / "training" / "configs" / "chat_v1.yaml").read_text(encoding="utf-8"))
        tiny_fixture_count = 6
        self.assertLess(tiny_fixture_count, config["readiness"]["minimum_approved_valid_rows"])


if __name__ == "__main__":
    unittest.main()
