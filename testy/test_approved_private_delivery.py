"""Tests for the narrowly scoped opt-in PM delivery adapter."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from rece.approved_private_delivery import ADBApprovedPrivateReplySender, DeliveryPreflight
from rece.delivery_state import DeliveryState
from rece.approved_private_delivery import OcrDeliveryVision
from oczy.local_ocr import OcrLayoutLine, ScreenOcrLayout, ScreenOcrResult


class FakeVision:
    def __init__(
        self,
        *,
        verified: bool = True,
        composer_inspected: bool = True,
        composer_empty: bool = True,
        confirmed: bool = True,
        before_signature: str = "",
    ) -> None:
        self.verified = verified
        self.composer_inspected = composer_inspected
        self.composer_empty = composer_empty
        self.confirmed = confirmed
        self.before_signature = before_signature
        self.calls: list[tuple[str, str]] = []

    def prepare_delivery(self, expected_alias: str, text: str) -> DeliveryPreflight:
        self.calls.append(("prepare", expected_alias))
        return DeliveryPreflight(
            self.verified,
            self.composer_inspected,
            self.composer_empty,
            self.before_signature,
        )

    def confirms_new_outgoing_reply(self, expected_alias: str, text: str, previous_signature: str) -> bool:
        self.calls.append(("confirm", expected_alias))
        return self.confirmed


class FakeBridge:
    def __init__(self, result: ScreenOcrResult) -> None:
        self.result = result
        self.captured = False

    def capture_visible_screen(self) -> None:
        self.captured = True

    def wait_for_result(self, *, timeout_seconds: float = 8.0) -> ScreenOcrResult:
        return self.result


class ApprovedPrivateDeliveryTests(unittest.TestCase):
    def test_refuses_before_any_adb_when_current_header_is_not_exact(self) -> None:
        runner_calls: list[object] = []
        sender = ADBApprovedPrivateReplySender(
            vision=FakeVision(verified=False),
            runner=lambda *args, **kwargs: runner_calls.append((args, kwargs)),
            pause=lambda _seconds: None,
        )

        receipt = sender.send_approved_reply("DOKTORB", "Hey there!")

        self.assertIs(receipt.state, DeliveryState.FAILED_SAFE)
        self.assertEqual(receipt.reason, "current_conversation_not_verified")
        self.assertFalse(runner_calls)

    def test_confirmed_send_uses_only_current_open_conversation(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def runner(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0)

        vision = FakeVision()
        sender = ADBApprovedPrivateReplySender(vision=vision, runner=runner, pause=lambda _seconds: None)

        receipt = sender.send_approved_reply("DOKTORB", "Hey, I saw that.")

        self.assertIs(receipt.state, DeliveryState.CONFIRMED)
        self.assertTrue(receipt.send_tap_attempted)
        self.assertEqual(vision.calls, [("prepare", "DOKTORB"), ("confirm", "DOKTORB")])
        commands = [call[0][0] for call in calls]
        self.assertEqual(commands[0][-4:-1], ["input", "tap", "176"])
        self.assertIn("ADB_INPUT_B64", commands[1])
        self.assertEqual(commands[-1][-4:-1], ["input", "tap", "816"])

    def test_missing_outgoing_echo_is_ambiguous_and_never_retried(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def runner(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0)

        sender = ADBApprovedPrivateReplySender(
            vision=FakeVision(confirmed=False),
            runner=runner,
            pause=lambda _seconds: None,
        )

        receipt = sender.send_approved_reply("DOKTORB", "Hey")

        self.assertIs(receipt.state, DeliveryState.AMBIGUOUS)
        self.assertTrue(receipt.send_tap_attempted)
        self.assertEqual(sum("ADB_INPUT_B64" in call[0][0] for call in calls), 3)

    def test_rejects_long_or_control_character_reply_without_touching_adb(self) -> None:
        runner_calls: list[object] = []
        sender = ADBApprovedPrivateReplySender(
            vision=FakeVision(),
            runner=lambda *args, **kwargs: runner_calls.append((args, kwargs)),
            pause=lambda _seconds: None,
        )

        receipt = sender.send_approved_reply("DOKTORB", "x" * 281)

        self.assertIs(receipt.state, DeliveryState.FAILED_SAFE)
        self.assertFalse(runner_calls)

    def test_human_draft_blocks_before_any_adb_touch(self) -> None:
        runner_calls: list[object] = []
        sender = ADBApprovedPrivateReplySender(
            vision=FakeVision(composer_empty=False),
            runner=lambda *args, **kwargs: runner_calls.append((args, kwargs)),
            pause=lambda _seconds: None,
        )

        receipt = sender.send_approved_reply("DOKTORB", "I should not overwrite this.")

        self.assertIs(receipt.state, DeliveryState.FAILED_SAFE)
        self.assertEqual(receipt.reason, "composer_has_manual_text")
        self.assertFalse(runner_calls)

    def test_unknown_composer_state_blocks_before_any_adb_touch(self) -> None:
        runner_calls: list[object] = []
        sender = ADBApprovedPrivateReplySender(
            vision=FakeVision(composer_inspected=False),
            runner=lambda *args, **kwargs: runner_calls.append((args, kwargs)),
            pause=lambda _seconds: None,
        )

        receipt = sender.send_approved_reply("DOKTORB", "Still do not touch it.")

        self.assertIs(receipt.state, DeliveryState.FAILED_SAFE)
        self.assertEqual(receipt.reason, "composer_state_not_confirmed")
        self.assertFalse(runner_calls)

    def test_ocr_confirmation_uses_the_same_right_bubble_boundary_as_eyes(self) -> None:
        result = ScreenOcrResult(
            "ok",
            "",
            1,
            "DOKTORB",
            ScreenOcrLayout(1000, (OcrLayoutLine("Hey, I saw that.", 302, 400, 700, 440),)),
        )
        bridge = FakeBridge(result)
        vision = OcrDeliveryVision(bridge_factory=lambda: bridge, pause=lambda _seconds: None)  # type: ignore[arg-type]

        self.assertTrue(vision.confirms_new_outgoing_reply("DOKTORB", "Hey, I saw that.", ""))
        self.assertTrue(bridge.captured)

    def test_ocr_does_not_confirm_an_identical_old_bubble(self) -> None:
        result = ScreenOcrResult(
            "ok",
            "",
            1,
            "DOKTORB",
            ScreenOcrLayout(1000, (OcrLayoutLine("Same text", 302, 400, 700, 440),)),
        )
        bridge = FakeBridge(result)
        vision = OcrDeliveryVision(bridge_factory=lambda: bridge, pause=lambda _seconds: None)  # type: ignore[arg-type]
        preflight = DeliveryPreflight(True, True, True, "302:400:700:440:same text")

        self.assertFalse(vision.confirms_new_outgoing_reply("DOKTORB", "Same text", preflight.outgoing_signature))


if __name__ == "__main__":
    unittest.main()
