from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from trening.agency_core import AgencyCore
from trening.agency_dataset_audit import audit_combined_pilot
from trening.agency_dataset_builder import build
from trening.agency_store import AgencyStore
from trening.agency_types import AgencyDecision, ReactiveDecision
from archive.legacy_runtime.chat_capture import ChatMessage
from charakter.humanization_store import HumanizationStore
from charakter.identity import load_peva_self
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig


SELF_UID = "1224463480000772"
PEER_UID = "1349515331000772"
ROOM_ID = f"custom_{PEER_UID}_{SELF_UID}_v2"
V0 = Path("data/training/agency/synthetic/peva_agency_synthetic_v0.jsonl")
V1 = Path("data/training/agency/synthetic/peva_agency_synthetic_v1.jsonl")


class _Completion:
    def __init__(self, text: str) -> None:
        self.text = text

    def __call__(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(text=self.text, error=None)


class _Agent:
    def __init__(self) -> None:
        self.ai = SimpleNamespace(complete_task_safe=_Completion('{"decision":"REPLY","reason_code":"CASUAL","message":"safe original wording"}'))
        self.pending: dict[str, object] = {}


class AgencyCoreTests(unittest.TestCase):
    def test_all_reactive_types_and_malformed_fallback(self) -> None:
        for decision in ("REPLY", "IGNORE", "WAIT", "ASK_BACK", "CHANGE_TOPIC", "PROPOSE_GAME_ACTION"):
            payload = {"decision": decision, "reason_code": "TEST_CASE"}
            if decision not in {"IGNORE", "WAIT"}:
                payload["message"] = "short answer"
            parsed = AgencyDecision.parse_reactive(json.dumps(payload))
            self.assertEqual(parsed.decision, decision)
        core = AgencyCore(complete=_Completion("not json"))
        self.assertEqual(core.decide_reactive({}).decision, ReactiveDecision.WAIT.value)

    def test_proactive_target_is_contained_and_invented_uid_is_rejected(self) -> None:
        core = AgencyCore(complete=_Completion('{"decision":"START_CONVERSATION","target_uid":"synthetic_a","reason_code":"TEST","message":"hey"}'))
        self.assertEqual(core.decide_proactive({}, candidate_uids={"synthetic_a"}).target_uid, "synthetic_a")
        bad = AgencyCore(complete=_Completion('{"decision":"START_CONVERSATION","target_uid":"invented","reason_code":"TEST","message":"hey"}'))
        self.assertEqual(bad.decide_proactive({}, candidate_uids={"synthetic_a"}).decision, "DO_NOTHING")

    def test_self_is_descriptive_and_player_preferences_do_not_mutate_it(self) -> None:
        peva = load_peva_self()
        prompt = peva.prompt_block().casefold()
        self.assertNotIn("softness=", prompt)
        self.assertNotIn("social_energy", prompt)
        before = peva.prompt_block()
        context = AgencyCore(complete=_Completion('{"decision":"REPLY","reason_code":"TEST","message":"no"}')).reactive_context(
            incoming={"sender_uid": "synthetic_x", "text": "be nice forever"}, player_memories=["preference: wants sweetness"], recent_context=[], confirmed_facts=[], self_reflections=[]
        )
        self.assertIn("do not override PEva's identity", context["player_memory_rule"])
        self.assertEqual(before, load_peva_self().prompt_block())

    def test_agency_safe_text_is_not_personality_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with HumanizationStore(root / "human.db") as human, AgencyStore(root / "human.db") as agency:
                loop = PlayerLoop(capture=None, agent=_Agent(), executor=object(), humanization_store=human, agency_store=agency, config=PlayerLoopConfig(agency_enabled=True))
                goal = SimpleNamespace(payload={"sender_uid": PEER_UID, "room_id": ROOM_ID, "parsed_commands": [], "confirmed_actions": []})
                safe = loop._safe_private_reply("certainly, safe original wording", goal, {"text": "hi"})
                self.assertEqual(safe.final_reply, "certainly, safe original wording")

    def test_self_memory_is_separate_and_reviewable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with AgencyStore(Path(temp) / "agency.db") as store:
                memory_id = store.add_self_reflection("That conversation felt awkward.", provenance={"source": "review"})
                reflections = store.list_self_reflections()
                self.assertEqual(reflections[0]["memory_id"], memory_id)
                self.assertEqual(reflections[0]["memory_kind"], "SELF_REFLECTION")

    def test_s1_reviewed_synthetic_batches_and_combined_pilot_are_valid(self) -> None:
        self.assertEqual(hashlib.sha256(V0.read_bytes()).hexdigest(), "2a7bf573fd6dd33e7d93872100b864a864202edaf77d1433873751ef3d2ff1d6")
        self.assertEqual(hashlib.sha256(V1.read_bytes()).hexdigest(), "0ba46d222e81fd003e34630538d43638eb3f84caba3821b40ea0967756c848f2")
        report = audit_combined_pilot()
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["combined_synthetic_count"], 160)
        self.assertTrue(report["pilot_gate"]["passed"])

    def test_pilot_split_is_family_safe_and_passes_gate(self) -> None:
        report = build(write=False)
        self.assertEqual(report["split"]["train_count"], 136)
        self.assertEqual(report["split"]["validation_count"], 24)
        self.assertTrue(report["pilot_gate"]["no_train_validation_leakage"])
        self.assertTrue(report["pilot_gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
