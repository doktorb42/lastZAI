"""Sprawdza lokalny panel bez otwierania przeglądarki ani modelu."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from contracts import MessageObservation, VisionResult
from mozg.lastz_ai import AIReply
from mozg.local_model import ModelStatus
from mozg.friendships import FriendshipStore
from oczy.identity import ConversationHeader
from peva_panel import LocalPanel
from peva_runtime import PEvaRuntime


class FakeModel:
    status = ModelStatus(True, False, "Fake", "fake.gguf", "not loaded")

    def start(self) -> ModelStatus:
        return self.status

    def close(self) -> None:
        return None

    def generate_reply_safe(self, incoming_text: str, **kwargs: object):
        raise AssertionError("Panel must not request a reply")


class ToggleModel(FakeModel):
    def __init__(self) -> None:
        self.status = ModelStatus(True, False, "Toggle", "toggle.gguf", "not loaded")

    def start(self) -> ModelStatus:
        self.status = ModelStatus(True, True, "Toggle", "toggle.gguf", "ready")
        return self.status

    def close(self) -> None:
        self.status = ModelStatus(True, False, "Toggle", "toggle.gguf", "stopped")


class PreviewModel(FakeModel):
    def __init__(self) -> None:
        self.status = ModelStatus(True, False, "Preview", "preview.gguf", "not loaded")

    def start(self) -> ModelStatus:
        self.status = ModelStatus(True, True, "Preview", "preview.gguf", "ready")
        return self.status

    def close(self) -> None:
        self.status = ModelStatus(True, False, "Preview", "preview.gguf", "stopped")

    def generate_reply_safe(self, incoming_text: str, **kwargs: object) -> AIReply:
        return AIReply(text="Hey, got it.")


class PanelTests(unittest.TestCase):
    def test_status_is_local_and_does_not_expose_chat_text(self) -> None:
        runtime = PEvaRuntime(model=FakeModel(), reader_factory=lambda: None)  # type: ignore[arg-type]
        runtime.start(load_model=False, start_eyes=False)
        panel = LocalPanel(runtime, port=0)
        self.addCleanup(panel.close)
        self.addCleanup(runtime.close)

        url = panel.start()
        with urllib.request.urlopen(url + "api/status", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertTrue(url.startswith("http://127.0.0.1:"))
        self.assertTrue(payload["safe"])
        self.assertFalse(payload["physical_actions_connected"])
        self.assertNotIn("hello", json.dumps(payload).lower())

    def test_preview_endpoint_shows_only_ephemeral_model_reply(self) -> None:
        runtime = PEvaRuntime(model=PreviewModel(), reader_factory=lambda: None)  # type: ignore[arg-type]
        runtime.start(load_model=True, start_eyes=False)
        runtime.process_vision(
            VisionResult(
                "PRIVATE_CONVERSATION",
                0.99,
                (MessageObservation("secret incoming", "OG doktorb", 0.99, "test"),),
            )
        )
        panel = LocalPanel(runtime, port=0)
        self.addCleanup(panel.close)
        self.addCleanup(runtime.close)

        url = panel.start()
        with urllib.request.urlopen(url + "api/status", timeout=2) as response:
            status = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(url + "api/preview", timeout=2) as response:
            preview = json.loads(response.read().decode("utf-8"))

        self.assertNotIn("secret incoming", json.dumps(status))
        self.assertEqual(preview, {"available": True, "decision": "REPLY", "text": "Hey, got it."})

    def test_panel_can_toggle_only_the_test_model_lifecycle(self) -> None:
        runtime = PEvaRuntime(model=ToggleModel(), reader_factory=lambda: None)  # type: ignore[arg-type]
        runtime.start(load_model=False, start_eyes=False)
        panel = LocalPanel(runtime, port=0)
        self.addCleanup(panel.close)
        self.addCleanup(runtime.close)

        url = panel.start()
        request = urllib.request.Request(url + "api/model/start", method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            started = json.loads(response.read().decode("utf-8"))
        self.assertTrue(started["model"]["ready"])
        self.assertTrue(started["safe"])

        request = urllib.request.Request(url + "api/model/stop", method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            stopped = json.loads(response.read().decode("utf-8"))
        self.assertFalse(stopped["model"]["ready"])
        self.assertTrue(stopped["safe"])

    def test_panel_records_friend_steps_without_any_game_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FriendshipStore(Path(directory) / "friends.json")
            candidate = store.observe_conversation(
                ConversationHeader("OG doktorb", "ROXY", 0.97),
                user_turns=6,
                assistant_turns=4,
                now_ms=1_000,
            )
            assert candidate is not None
            runtime = PEvaRuntime(
                model=FakeModel(),
                reader_factory=lambda: None,  # type: ignore[arg-type]
                friendships=store,
            )
            runtime.start(load_model=False, start_eyes=False)
            panel = LocalPanel(runtime, port=0)
            self.addCleanup(panel.close)
            self.addCleanup(runtime.close)

            url = panel.start()
            with urllib.request.urlopen(url + "api/friends", timeout=2) as response:
                candidates = json.loads(response.read().decode("utf-8"))["candidates"]
            self.assertEqual(candidates[0]["status"], "REVIEW")
            self.assertEqual(candidates[0]["draft_message"], "Hey, would you like to be friends?")

            def update(action: str, payload: dict[str, str] | None = None) -> dict[str, object]:
                request = urllib.request.Request(
                    url + f"api/friends/{candidate.candidate_id}/{action}",
                    data=json.dumps(payload or {}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    return json.loads(response.read().decode("utf-8"))

            self.assertEqual(update("offer-sent")["status"], "OFFER_SENT")
            self.assertEqual(update("friend-added")["status"], "NEEDS_ALIAS")
            confirmed = update("confirm-alias", {"alias": "DOKTORB"})
            self.assertEqual(confirmed["status"], "FRIEND")
            self.assertEqual(confirmed["final_alias"], "DOKTORB")
            approved = update("approve-auto-reply")
            self.assertTrue(approved["auto_reply_approved"])
            removed = update("remove")
            self.assertEqual(removed["final_alias"], "DOKTORB")
            with urllib.request.urlopen(url + "api/friends", timeout=2) as response:
                self.assertEqual(json.loads(response.read().decode("utf-8"))["candidates"], [])
            self.assertEqual(runtime.status_snapshot()["last_action"], "NONE")

    def test_panel_can_register_an_existing_manually_verified_friend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FriendshipStore(Path(directory) / "friends.json")
            runtime = PEvaRuntime(
                model=FakeModel(),
                reader_factory=lambda: None,  # type: ignore[arg-type]
                friendships=store,
            )
            runtime.start(load_model=False, start_eyes=False)
            panel = LocalPanel(runtime, port=0)
            self.addCleanup(panel.close)
            self.addCleanup(runtime.close)

            url = panel.start()
            request = urllib.request.Request(
                url + "api/friends/new/register",
                data=json.dumps({"alias": "DOCTOR TEST"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                friend = json.loads(response.read().decode("utf-8"))

            self.assertEqual(friend["status"], "FRIEND")
            self.assertFalse(friend["auto_reply_approved"])


if __name__ == "__main__":
    unittest.main()
