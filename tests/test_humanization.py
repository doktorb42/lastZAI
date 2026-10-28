from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mozg.action_claim_guard import ActionClaimGuard
from archive.legacy_runtime.agent import AgentConfig, LastZAgent
from trening.conversation_review import render_player_review
from mozg.conversation_store import ConversationStore
from archive.legacy_runtime.goal_manager import Goal, GoalManager, GoalType
from charakter.humanization_store import HumanizationStore
from mozg.reply_naturalness import NaturalnessVerdict, ReplyNaturalness
from emocje.response_delay import ResponseDelayManager
from charakter.social_initiative import SocialInitiativeManager, negative_social_signal
from archive.legacy_runtime.player_loop import PlayerLoop, PlayerLoopConfig


class _QueueAI:
    @staticmethod
    def normalize_reply(text: str) -> str:
        return " ".join(str(text).split())


class HumanizationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = HumanizationStore(self.root / "humanization.db", training_root=self.root / "training")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_style_profile_load_save_and_review(self) -> None:
        style = self.store.update_style("p1", softness=.8, tags=["playful", "soft"])
        self.assertEqual(style.softness, .8)
        candidate = self.store.create_style_candidate(player_uid="p1", changes={"emoji_level": "HIGH"}, source_text="more emoji")
        approved = self.store.approve_style_candidate(candidate)
        self.assertEqual(approved.emoji_level, "HIGH")

    def test_memory_provenance_edit_without_deletion_and_history(self) -> None:
        candidate = self.store.create_memory_candidate(
            player_uid="p1", memory_type="GAME_PREFERENCE", content="Prefers riders",
            source_message_key="room:1", source_text="I prefer riders", confidence=.9, importance=.8,
        )
        memory_id = self.store.approve_memory_candidate(candidate)
        self.store.edit_memory(memory_id, content="Prefers riders for events", reason="clarified")
        self.store.edit_memory(memory_id, active=False, reason="outdated")
        memory = self.store.get_memories("p1", include_inactive=True)[0]
        self.assertFalse(memory.active)
        self.assertEqual(memory.source_text, "I prefer riders")
        self.assertGreaterEqual(len(self.store.memory_history(memory_id)), 2)

    def test_relationship_persistence_and_familiarity_bounds(self) -> None:
        self.store.set_relationship("p2", "ALLY")
        self.store.set_familiarity("p2", 1.0)
        self.assertEqual(self.store.get_profile("p2").relationship, "ALLY")
        for _ in range(50):
            self.store.observe_incoming(player_uid="p2", room_id="custom_p2_self_v2", text="This is a meaningful message", at=1.0)
        self.assertLessEqual(self.store.get_profile("p2").familiarity, .95)

    def test_multiline_memory_is_preserved(self) -> None:
        memory_id = self.store.add_memory(player_uid="p3", memory_type="SOCIAL", content="first line\nsecond line")
        self.assertEqual(self.store.get_memories("p3")[0].memory_id, memory_id)
        self.assertIn("\n", self.store.get_memories("p3")[0].content)

    def test_review_display_includes_style_memory_and_context(self) -> None:
        self.store.add_memory(player_uid="p4", memory_type="IDENTITY", content="Name is Alex")
        with ConversationStore(self.root / "conversation.db") as conversation:
            text = render_player_review(self.store, conversation, "p4")
        self.assertIn("STYLE", text)
        self.assertIn("ACTIVE MEMORY", text)
        self.assertIn("Name is Alex", text)

    def test_bad_reason_and_edited_reply_persist(self) -> None:
        review_id = self.store.record_reply_review(player_uid="p5", room_id="r", incoming_text="hi", reply_text="hello", original_reply="Hello there")
        self.store.review_reply(review_id, status="BAD", bad_reason="TOO_FORMAL")
        self.assertEqual(self.store.get_reply_review(review_id)["bad_reason"], "TOO_FORMAL")
        self.store.review_reply(review_id, status="EDITED", edited_reply="yo")
        self.assertEqual(self.store.get_reply_review(review_id)["edited_reply"], "yo")


class HumanizationPolicyTests(unittest.TestCase):
    def test_boilerplate_and_recent_repetition_detection(self) -> None:
        checker = ReplyNaturalness()
        self.assertEqual(checker.evaluate("How can I help you?").verdict, NaturalnessVerdict.REWRITE_RECOMMENDED)
        self.assertEqual(checker.evaluate("yo you on?", recent_replies=["yo you on?"]).reason, "recent_reply_repetition")

    def test_action_safety_remains_authoritative_after_any_rewrite(self) -> None:
        guard = ActionClaimGuard()
        verdict = guard.validate("joined the rally", pending_command={"command_type": "JOIN_RALLY"}, confirmed_actions=[])
        self.assertTrue(verdict.changed)

    def test_response_delay_is_scheduled_and_non_blocking(self) -> None:
        manager = ResponseDelayManager(rng=random.Random(3))
        delay = manager.schedule(now=100.0, text="hey")
        self.assertGreater(delay.due_at, 100.0)
        self.assertEqual(delay.category, "casual")

    def test_new_message_can_extend_existing_goal_delay(self) -> None:
        goals = GoalManager()
        first = goals.add_goal(Goal(GoalType.ANSWER_PRIVATE_MESSAGE, payload={"dedupe_key": "p"}, not_before=5.0))
        goals.add_goal(Goal(GoalType.ANSWER_PRIVATE_MESSAGE, payload={"dedupe_key": "p"}, not_before=8.0))
        self.assertEqual(goals.get_goal(first.goal_id).not_before, 8.0)

    def test_negative_signals_are_conservative(self) -> None:
        self.assertEqual(negative_social_signal("brb"), (False, False))
        self.assertEqual(negative_social_signal("busy right now"), (True, False))
        self.assertEqual(negative_social_signal("leave me alone"), (True, True))

    def test_unknown_player_never_proactively_pings(self) -> None:
        manager = SocialInitiativeManager(rng=random.Random(1), now=lambda: 10_000.0)
        result = manager.choose([{"player_uid": "p", "last_room_id": "r", "relationship": "UNKNOWN", "familiarity": 1, "last_incoming_at": 1}], proactive_last_hour=0)
        self.assertIsNone(result.candidate)
        self.assertEqual(result.reason, "unknown_player")

    def test_friend_can_become_eligible_under_seeded_probability(self) -> None:
        manager = SocialInitiativeManager(rng=random.Random(1), now=lambda: 10_000.0)
        result = manager.choose([{"player_uid": "p", "last_room_id": "r", "relationship": "FRIEND", "familiarity": .95, "conversation_interest_score": .8, "last_incoming_at": 1}], proactive_last_hour=0)
        self.assertIsNotNone(result.candidate)

    def test_cooldowns_and_lane_block_prevent_proactive_candidate(self) -> None:
        manager = SocialInitiativeManager(rng=random.Random(1), now=lambda: 10_000.0)
        profile = {"player_uid": "p", "last_room_id": "r", "relationship": "FRIEND", "familiarity": 1, "last_incoming_at": 1, "last_proactive_at": 9_900}
        self.assertEqual(manager.choose([profile], proactive_last_hour=0).reason, "cooldown")
        profile.pop("last_proactive_at")
        self.assertEqual(manager.choose([profile], proactive_last_hour=0, send_lane_busy=True).reason, "send_lane_busy")

    def test_same_topic_cooldown_and_explicit_suppression(self) -> None:
        manager = SocialInitiativeManager(rng=random.Random(1), now=lambda: 20_000.0)
        profile = {"player_uid": "p", "last_room_id": "r", "relationship": "FRIEND", "familiarity": 1, "last_incoming_at": 1, "last_proactive_at": 8_000, "last_proactive_topic": "work"}
        self.assertEqual(manager.choose([profile], proactive_last_hour=0).reason, "same_topic_cooldown")
        profile["proactive_suppressed_indefinitely"] = 1
        self.assertEqual(manager.choose([profile], proactive_last_hour=0).reason, "explicit_opt_out")

    def test_probability_and_social_energy_are_reproducible(self) -> None:
        profile = {"player_uid": "p", "last_room_id": "r", "relationship": "KNOWN_PLAYER", "familiarity": .2, "last_incoming_at": 1}
        first = SocialInitiativeManager(rng=random.Random(999), now=lambda: 10_000.0).choose([profile], proactive_last_hour=0)
        second = SocialInitiativeManager(rng=random.Random(999), now=lambda: 10_000.0).choose([profile], proactive_last_hour=0)
        self.assertEqual(first.reason, second.reason)
        energetic = SocialInitiativeManager(rng=random.Random(1), now=lambda: 10_000.0)
        for _ in range(12): energetic.note_reply(at=10_000.0)
        self.assertLess(energetic.social_energy, 1.0)


class ProactiveQueueSafetyTests(unittest.TestCase):
    def test_proactive_queue_requires_exact_direct_uid_and_room(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with ConversationStore(Path(temp) / "conversation.db") as store:
                own_uid = "1224463480000772"
                peer_uid = "1349515331000772"
                agent = LastZAgent(ai=_QueueAI(), store=store, config=AgentConfig(own_uid=own_uid))
                pending = agent.queue_proactive_reply(player_uid=peer_uid, room_id=f"custom_{peer_uid}_{own_uid}_v2", text="yo")
                self.assertEqual(pending.player_uid, peer_uid)
                with self.assertRaises(ValueError):
                    agent.queue_proactive_reply(player_uid=peer_uid, room_id=f"custom_999_{own_uid}_v2", text="yo")

    def test_proactive_preview_never_calls_transport(self) -> None:
        class AI:
            @staticmethod
            def complete_task_safe(**_kwargs): return SimpleNamespace(text="yo you on?", error=None)
            @staticmethod
            def normalize_reply(text): return str(text).strip()
        class Agent:
            ai = AI()
            config = SimpleNamespace(own_uid="1224463480000772")
        class Executor:
            def __init__(self): self.calls = 0
            def execute(self, *_args): self.calls += 1
        with tempfile.TemporaryDirectory() as temp:
            store = HumanizationStore(Path(temp) / "humanization.db")
            try:
                uid, own = "1349515331000772", "1224463480000772"
                store.set_relationship(uid, "FRIEND")
                store.set_familiarity(uid, .9)
                executor = Executor()
                loop = PlayerLoop(
                    capture=None, agent=Agent(), executor=executor,
                    humanization_store=store,
                    config=PlayerLoopConfig(
                        humanization_enabled=True,
                        proactive_enabled=True,
                        proactive_auto_send=False,
                        auto_send=False,
                        agency_enabled=False,
                    ),
                    logger=lambda _line: None,
                )
                goal = Goal(GoalType.PROACTIVE_PRIVATE_MESSAGE, payload={
                    "sender_uid": uid, "room_id": f"custom_{uid}_{own}_v2",
                    "proactive_candidate": {"initiative_type": "FOLLOW_UP", "reason": "open_conversation_interest", "score": .4, "topic": None},
                })
                loop.goal_manager.add_goal(goal)
                loop._generate_proactive_reply(goal)
                self.assertEqual(executor.calls, 0)
                self.assertEqual(store.proactive_count_since(0), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
