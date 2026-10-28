from __future__ import annotations

import unittest

from contracts import DecisionKind
from mozg.lastz_ai import AIReply
from mozg.local_model import ModelStatus
from oczy.local_ocr import OcrLayoutLine, ScreenOcrLayout
from oczy.packets import ChatPacket
from oczy.session_history import (
    ConversationTurn,
    SessionHistory,
    extract_visible_turns,
    has_trailing_animated_emote,
)
from peva_runtime import PEvaRuntime


def _layout(*rows: tuple[str, int, int, int, int]) -> ScreenOcrLayout:
    return ScreenOcrLayout(1_000, tuple(OcrLayoutLine(*row) for row in rows))


class FakeModel:
    def __init__(self) -> None:
        self.status = ModelStatus(True, False, "Fake", "fake.gguf", "not started")
        self.calls: list[tuple[str, dict[str, object]]] = []

    def start(self) -> ModelStatus:
        self.status = ModelStatus(True, True, "Fake", "fake.gguf", "ready")
        return self.status

    def close(self) -> None:
        self.status = ModelStatus(True, False, "Fake", "fake.gguf", "stopped")

    def generate_reply_safe(self, incoming_text: str, **kwargs: object) -> AIReply:
        self.calls.append((incoming_text, dict(kwargs)))
        return AIReply(text="I saw that.")


class SessionHistoryTests(unittest.TestCase):
    def test_extracts_only_confident_left_right_chat_lines(self) -> None:
        layout = _layout(
            ("LV.27 [ROXY]OG doktorb", 80, 20, 400, 60),
            ("hello there", 120, 120, 330, 155),
            ("I am here", 350, 180, 560, 215),
            ("what are you doing?", 120, 260, 410, 295),
            ("01:55", 470, 330, 540, 360),
            ("uncertain middle", 290, 400, 540, 435),
        )

        self.assertEqual(
            extract_visible_turns(layout),
            (
                ConversationTurn("user", "hello there"),
                ConversationTurn("assistant", "I am here"),
                ConversationTurn("user", "what are you doing?"),
            ),
        )

    def test_corrupted_sender_header_is_not_injected_into_model_history(self) -> None:
        layout = _layout(
            ("8 LV.27O DOKTORB", 80, 20, 400, 60),
            ("hello there", 120, 90, 330, 125),
        )

        self.assertEqual(
            extract_visible_turns(layout),
            (ConversationTurn("user", "hello there"),),
        )

    def test_corrupted_sender_header_cannot_start_a_private_session(self) -> None:
        history = SessionHistory()
        layout = _layout(("hello there", 120, 90, 330, 125))

        self.assertIsNone(history.observe("8 LV.27O DOKTORB", layout))

    def test_session_merges_screen_overlap_without_storing_a_file(self) -> None:
        history = SessionHistory()
        header = "LV.27 [ROXY]OG doktorb"
        first = _layout(
            ("hello", 100, 100, 220, 135),
            ("hey", 350, 160, 440, 195),
            ("want to raid later?", 100, 220, 360, 255),
        )
        second = _layout(
            ("hey", 350, 100, 440, 135),
            ("want to raid later?", 100, 160, 360, 195),
            ("I can join after dinner", 100, 220, 420, 255),
        )

        first_view = history.observe(header, first)
        second_view = history.observe(header, second)

        self.assertIsNotNone(first_view)
        assert first_view is not None
        self.assertEqual(first_view.incoming_text, "want to raid later?")
        self.assertIsNotNone(second_view)
        assert second_view is not None
        self.assertEqual(second_view.incoming_text, "I can join after dinner")
        self.assertEqual(
            [(turn.role, turn.content) for turn in second_view.history],
            [
                ("user", "hello"),
                ("assistant", "hey"),
                ("user", "want to raid later?"),
            ],
        )

    def test_plain_title_from_dedicated_header_can_start_a_temporary_session(self) -> None:
        history = SessionHistory()
        layout = _layout(("hello there", 100, 100, 320, 135))

        view = history.observe("DOKTORB", layout)

        self.assertIsNotNone(view)
        assert view is not None
        self.assertEqual(view.incoming_text, "hello there")

    def test_uses_bubble_position_and_joins_a_long_multiline_message(self) -> None:
        layout = _layout(
            ("LV.24 AsifAlKawchar", 90, 20, 380, 55),
            ("when you didn't reach the", 105, 80, 610, 116),
            ("helicopter then you have to", 105, 120, 630, 156),
            ("collect as soon as possible.", 105, 160, 620, 196),
            ("LV.26 [ROXY]OG PEva", 360, 230, 750, 266),
            ("I start clicking before 15 sec", 330, 285, 790, 321),
        )

        self.assertEqual(
            extract_visible_turns(layout),
            (
                ConversationTurn(
                    "user",
                    "when you didn't reach the helicopter then you have to collect as soon as possible.",
                ),
                ConversationTurn("assistant", "I start clicking before 15 sec"),
            ),
        )

    def test_ignores_event_cards_gifts_and_reply_quote_content(self) -> None:
        layout = _layout(
            ("Headquarters", 105, 20, 330, 55),
            ("Lv.22[LbT]Tunf81 [State", 110, 60, 490, 95),
            ("775 X:132 Y:224]", 110, 100, 380, 135),
            ("Give gifts", 160, 180, 310, 215),
            ("The lucky number is our lucky day!", 160, 220, 570, 255),
            ("Reply killer 72: He needs to…", 110, 300, 540, 335),
            ("فِيهِ وَقْتٍ أَكْثَر", 110, 340, 420, 375),
            ("hello", 105, 440, 230, 475),
            ("Hello!", 650, 510, 780, 545),
        )

        self.assertEqual(
            extract_visible_turns(layout),
            (
                ConversationTurn("user", "hello"),
                ConversationTurn("assistant", "Hello!"),
            ),
        )

    def test_ignores_illustrated_fire_emote_and_never_replies_to_an_older_turn(self) -> None:
        history = SessionHistory()
        header = "LV.27 [ROXY]Cute Face"
        first = _layout(("hello", 105, 80, 230, 115))
        emote = _layout(
            ("hello", 105, 80, 230, 115),
            ("LV.27 [ROXY]Cute Face", 85, 180, 410, 215),
            ("Just knocked out fire number 5 for ya. Call me", 105, 225, 620, 260),
            ("the flame tamer!", 105, 265, 355, 300),
        )

        self.assertIsNotNone(history.observe(header, first))
        self.assertTrue(has_trailing_animated_emote(emote))
        self.assertEqual(extract_visible_turns(emote), (ConversationTurn("user", "hello"),))
        self.assertIsNone(history.observe(header, emote))

    def test_runtime_uses_structured_history_and_keeps_hands_disconnected(self) -> None:
        model = FakeModel()
        runtime = PEvaRuntime(model=model, reader_factory=lambda: None)  # type: ignore[arg-type]
        runtime.start(load_model=True, start_eyes=False)
        self.addCleanup(runtime.close)
        packet = ChatPacket(
            "untrusted full screen text",
            1,
            True,
            "visible_message_complete",
            1,
            conversation_header="LV.27 [ROXY]OG doktorb",
            layout=_layout(
                ("hello", 100, 100, 220, 135),
                ("hey", 350, 160, 440, 195),
                ("are you joining later?", 100, 220, 430, 255),
            ),
        )

        decision = runtime.process_packet(packet)

        self.assertIs(decision.kind, DecisionKind.REPLY)
        self.assertEqual([text for text, _ in model.calls], ["are you joining later?"])
        self.assertEqual(
            model.calls[0][1]["history"],
            (
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hey"},
            ),
        )
        self.assertEqual(runtime.status_snapshot()["history_turns_in_memory"], 2)
        self.assertEqual(runtime.status_snapshot()["last_action"], "NONE")


if __name__ == "__main__":
    unittest.main()
