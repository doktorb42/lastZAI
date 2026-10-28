"""Testy centralnego runtime PEva bez BlueStacks, modelu ani sieci."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from contracts import ActionKind, Decision, DecisionKind, MessageObservation, VisionResult
from mozg.lastz_ai import AIReply
from mozg.local_model import ModelStatus
from mozg.friendships import FriendshipStore
from oczy.packets import ChatPacket
from peva_runtime import PEvaRuntime
from rece import DeliveryReceipt, DeliveryState


class FakeModel:
    def __init__(self) -> None:
        self.status = ModelStatus(True, False, "Fake PEva", "fake.gguf", "not started")
        self.calls: list[str] = []
        self.call_kwargs: list[dict[str, object]] = []

    def start(self) -> ModelStatus:
        self.status = ModelStatus(True, True, "Fake PEva", "fake.gguf", "ready")
        return self.status

    def close(self) -> None:
        self.status = ModelStatus(True, False, "Fake PEva", "fake.gguf", "stopped")

    def generate_reply_safe(self, incoming_text: str, **kwargs: object) -> AIReply:
        self.calls.append(incoming_text)
        self.call_kwargs.append(dict(kwargs))
        return AIReply(text="Hey, got it.")


class NoopReader:
    def poll_once(self):
        return SimpleNamespace(kind="no_packet", packet=None, detail="")


class FakeSender:
    def __init__(self, receipt: DeliveryReceipt | None = None) -> None:
        self.receipt = receipt or DeliveryReceipt(DeliveryState.CONFIRMED, "test", True, True)
        self.calls: list[tuple[str, str]] = []

    def send_approved_reply(self, expected_alias: str, text: str) -> DeliveryReceipt:
        self.calls.append((expected_alias, text))
        return self.receipt


class RuntimeTests(unittest.TestCase):
    def _runtime(self) -> tuple[PEvaRuntime, FakeModel]:
        model = FakeModel()
        runtime = PEvaRuntime(model=model, reader_factory=NoopReader)
        runtime.start(load_model=True, start_eyes=False)
        self.addCleanup(runtime.close)
        return runtime, model

    def test_runtime_starts_safe_and_never_connects_physical_hands(self) -> None:
        runtime, _ = self._runtime()

        snapshot = runtime.status_snapshot()

        self.assertTrue(snapshot["safe"])
        self.assertTrue(snapshot["model"]["ready"])
        self.assertFalse(snapshot["physical_actions_connected"])
        self.assertEqual(snapshot["last_action"], ActionKind.NONE.value)

    def test_ocr_packet_without_confirmed_player_waits_without_model_call(self) -> None:
        runtime, model = self._runtime()
        packet = ChatPacket("hello PEva", 1, True, "visible_message_complete", 1)

        decision = runtime.process_packet(packet)

        self.assertEqual(decision.reason, "conversation_header_not_confirmed")
        self.assertFalse(model.calls)
        self.assertEqual(runtime.status_snapshot()["last_action"], ActionKind.NONE.value)

    def test_confirmed_identity_may_create_preview_but_not_send_action(self) -> None:
        runtime, model = self._runtime()
        vision = VisionResult(
            "PRIVATE_CONVERSATION",
            0.99,
            (MessageObservation("hello PEva", "OG doktorb", 0.99, "test"),),
        )

        decision = runtime.process_vision(vision)

        self.assertEqual(decision.kind.value, "REPLY")
        self.assertEqual(decision.text, "Hey, got it.")
        self.assertEqual(model.calls, ["hello PEva"])
        snapshot = runtime.status_snapshot()
        self.assertEqual(snapshot["last_action"], ActionKind.NONE.value)
        self.assertFalse(snapshot["physical_actions_connected"])
        self.assertNotIn("Hey, got it.", str(snapshot))
        self.assertEqual(
            runtime.preview_snapshot(),
            {"available": True, "decision": "REPLY", "text": "Hey, got it."},
        )

    def test_preverified_memory_context_reaches_model_without_runtime_lookup_by_name(self) -> None:
        runtime, model = self._runtime()
        vision = VisionResult(
            "PRIVATE_CONVERSATION",
            0.99,
            (MessageObservation("hello PEva", "OG doktorb", 0.99, "test"),),
        )

        decision = runtime.process_vision(
            vision,
            memory_context=("Verified UID memory: prefers short answers.",),
        )

        self.assertEqual(decision.kind.value, "REPLY")
        self.assertEqual(
            model.call_kwargs[-1]["extra_context"][-1],
            "Verified UID memory: prefers short answers.",
        )

    def test_only_exact_human_approved_alias_can_enter_delivery_lane(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            store = FriendshipStore(Path(directory) / "friends.json")
            friend = store.register_confirmed_friend("DOKTORB")
            store.set_auto_reply_approval(friend.candidate_id, True)
            sender = FakeSender()
            runtime = PEvaRuntime(
                model=FakeModel(),
                reader_factory=NoopReader,
                friendships=store,
                sender=sender,
            )
            runtime.start(load_model=True, start_eyes=False)
            self.addCleanup(runtime.close)
            runtime.resume_actions("test")
            decision = Decision(DecisionKind.REPLY, "test", "Yep, I am here.")

            runtime._maybe_deliver_approved_reply(decision, "DOKTORB")

            self.assertEqual(sender.calls, [("DOKTORB", "Yep, I am here.")])
            self.assertEqual(runtime.status_snapshot()["last_action"], ActionKind.SEND_MESSAGE.value)

    def test_unapproved_alias_stays_preview_only_even_when_safe_is_released(self) -> None:
        sender = FakeSender()
        runtime = PEvaRuntime(model=FakeModel(), reader_factory=NoopReader, sender=sender)
        runtime.start(load_model=True, start_eyes=False)
        self.addCleanup(runtime.close)
        runtime.resume_actions("test")

        runtime._maybe_deliver_approved_reply(
            Decision(DecisionKind.REPLY, "test", "Hey!"),
            "SOMEONE ELSE",
        )

        self.assertFalse(sender.calls)
        self.assertEqual(runtime.status_snapshot()["last_action"], ActionKind.NONE.value)

    def test_ambiguous_send_stops_future_automatic_replies(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            store = FriendshipStore(Path(directory) / "friends.json")
            friend = store.register_confirmed_friend("DOKTORB")
            store.set_auto_reply_approval(friend.candidate_id, True)
            sender = FakeSender(DeliveryReceipt(DeliveryState.AMBIGUOUS, "no visual echo", True, True))
            runtime = PEvaRuntime(
                model=FakeModel(), reader_factory=NoopReader, friendships=store, sender=sender,
            )
            runtime.start(load_model=True, start_eyes=False)
            self.addCleanup(runtime.close)
            runtime.resume_actions("test")

            runtime._maybe_deliver_approved_reply(Decision(DecisionKind.REPLY, "test", "Hey!"), "DOKTORB")

            self.assertTrue(runtime.status_snapshot()["safe"])
            self.assertEqual(runtime.status_snapshot()["automatic_delivery"]["last_state"], "AMBIGUOUS")

    def test_model_can_be_stopped_and_restarted_for_a_test_while_runtime_stays_safe(self) -> None:
        runtime, model = self._runtime()

        runtime.stop_model()

        self.assertTrue(runtime.is_running)
        self.assertFalse(model.status.ready)
        self.assertTrue(runtime.status_snapshot()["safe"])

        status = runtime.start_model()

        self.assertTrue(status.ready)
        self.assertTrue(runtime.status_snapshot()["safe"])

    def test_burst_waits_before_it_reaches_brain(self) -> None:
        packet = ChatPacket("hello", 1, True, "visible_message_complete", 1)

        class Reader:
            def __init__(self) -> None:
                self.count = 0

            def poll_once(self):
                self.count += 1
                if self.count == 1:
                    return SimpleNamespace(kind="packet_read", packet=packet, detail="")
                return SimpleNamespace(kind="no_packet", packet=None, detail="")

        model = FakeModel()
        runtime = PEvaRuntime(model=model, reader_factory=Reader)
        runtime.start(load_model=True, start_eyes=False)
        self.addCleanup(runtime.close)

        self.assertIsNone(runtime.poll_eyes_once(now=10.0))
        # PEva keeps a short two-second quiet window so "hey" can be followed
        # by the actual question without generating an interrupting reply.
        decision = runtime.poll_eyes_once(now=12.1)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "conversation_header_not_confirmed")

    def test_profile_overlay_discards_a_waiting_burst_without_calling_the_model(self) -> None:
        packet = ChatPacket("hello", 1, True, "visible_message_complete", 1)

        class Reader:
            def __init__(self) -> None:
                self.count = 0

            def poll_once(self):
                self.count += 1
                if self.count == 1:
                    return SimpleNamespace(kind="packet_read", packet=packet, detail="")
                return SimpleNamespace(
                    kind="overlay_open",
                    packet=None,
                    detail="Okno profilu zasłania rozmowę; PEva pomija ten skan.",
                )

        model = FakeModel()
        runtime = PEvaRuntime(model=model, reader_factory=Reader)
        runtime.start(load_model=True, start_eyes=False)
        self.addCleanup(runtime.close)

        self.assertIsNone(runtime.poll_eyes_once(now=10.0))
        self.assertIsNone(runtime.poll_eyes_once(now=12.0))
        self.assertFalse(model.calls)


if __name__ == "__main__":
    unittest.main()
