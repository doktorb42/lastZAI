"""Tests for PEva's small offline Last Z reference layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contracts import MessageObservation, VisionResult
from mozg.game_knowledge import GameKnowledgeBase
from mozg.lastz_ai import AIReply
from mozg.local_model import ModelStatus
from peva_runtime import PEvaRuntime


class FakeModel:
    def __init__(self) -> None:
        self.status = ModelStatus(True, True, "Fake", "fake.gguf", "ready")
        self.context: tuple[str, ...] = ()

    def start(self) -> ModelStatus:
        return self.status

    def close(self) -> None:
        return None

    def generate_reply_safe(self, incoming_text: str, **kwargs: object) -> AIReply:
        self.context = tuple(kwargs.get("extra_context", ()))
        return AIReply(text="Check the requirement panel first.")


class GameKnowledgeTests(unittest.TestCase):
    def test_game_question_receives_only_relevant_local_note(self) -> None:
        knowledge = GameKnowledgeBase.load()

        notes = knowledge.for_message("What do I need for my HQ upgrade?")

        self.assertEqual(len(notes), 1)
        self.assertIn("Headquarters upgrades", notes[0])
        self.assertEqual(knowledge.for_message("hey, how are you today"), ())

    def test_malformed_file_is_ignored_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("not-json", encoding="utf-8")

            self.assertEqual(GameKnowledgeBase.load(path).snapshot(), ())

    def test_runtime_passes_note_to_model_only_for_game_question(self) -> None:
        model = FakeModel()
        runtime = PEvaRuntime(model=model, reader_factory=lambda: None)  # type: ignore[arg-type]
        runtime.start(load_model=False, start_eyes=False)
        self.addCleanup(runtime.close)

        runtime.process_vision(
            VisionResult(
                "PRIVATE_CONVERSATION",
                0.99,
                (MessageObservation("What do I need for an HQ upgrade?", "DOKTORB", 0.99, "test"),),
            )
        )

        self.assertTrue(any(note.startswith("COMMUNITY GAME NOTE") for note in model.context))
        self.assertEqual(runtime.status_snapshot()["local_game_knowledge_facts"], 5)


if __name__ == "__main__":
    unittest.main()
