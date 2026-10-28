"""Very small, opt-in delivery lane for a currently open approved PM.

This module intentionally cannot browse the private-chat list or select a
recipient.  The runtime may call it only after Oczy have just read a structured
conversation whose *exact local alias* is approved by the human.  It performs
one more one-shot OCR header check immediately before entering text, then uses
ADBKeyboard to type and tap Send.  The same pre-send OCR pass also proves that
the human has not already typed a draft. A second OCR pass must show a *new*
PEva bubble. Missing confirmation is treated as ambiguous and never retried.
"""

from __future__ import annotations

import base64
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from config.runtime_config import ADB_DEVICE, ADB_PATH, SCREEN_HEIGHT, SCREEN_WIDTH
from oczy.identity import is_blocking_overlay_title, parse_private_header
from oczy.local_ocr import OcrLayoutLine, ScreenOcrBridge, ScreenOcrResult

from .delivery_state import DeliveryState


MAX_AUTOMATIC_REPLY_CHARS = 280


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """Facts about one delivery attempt, without retaining chat text."""

    state: DeliveryState
    reason: str
    text_entered: bool = False
    send_tap_attempted: bool = False


@dataclass(frozen=True, slots=True)
class DeliveryPreflight:
    """One local visual check made before PEva can touch the composer."""

    conversation_verified: bool
    composer_inspected: bool
    composer_empty: bool
    outgoing_signature: str


class DeliveryVision(Protocol):
    """One-shot visual proof used immediately around an outgoing reply."""

    def prepare_delivery(self, expected_alias: str, text: str) -> DeliveryPreflight: ...

    def confirms_new_outgoing_reply(
        self,
        expected_alias: str,
        text: str,
        previous_signature: str,
    ) -> bool: ...


class ApprovedPrivateReplySender(Protocol):
    """Narrow physical interface exposed to the runtime."""

    def send_approved_reply(self, expected_alias: str, text: str) -> DeliveryReceipt: ...


class OcrDeliveryVision:
    """Use two ephemeral Android-Helper OCR reads; never save screenshots."""

    def __init__(
        self,
        *,
        bridge_factory: Callable[[], ScreenOcrBridge] = ScreenOcrBridge,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._bridge_factory = bridge_factory
        self._pause = pause

    def prepare_delivery(self, expected_alias: str, text: str) -> DeliveryPreflight:
        result = self._scan()
        if result is None or not _result_has_alias(result, expected_alias):
            return DeliveryPreflight(False, False, False, "")
        inspected = bool(result.composer_inspected)
        return DeliveryPreflight(
            conversation_verified=True,
            composer_inspected=inspected,
            composer_empty=inspected and _composer_is_empty(result.composer_text),
            outgoing_signature=_outgoing_reply_signature(result, text),
        )

    def confirms_new_outgoing_reply(
        self,
        expected_alias: str,
        text: str,
        previous_signature: str,
    ) -> bool:
        # Last Z needs a short repaint after the Send tap.  This is not a
        # listener or a retry loop: it is one bounded, local proof attempt.
        self._pause(0.45)
        result = self._scan()
        if result is None or not _result_has_alias(result, expected_alias):
            return False
        signature = _outgoing_reply_signature(result, text)
        return bool(signature) and signature != previous_signature

    def _scan(self) -> ScreenOcrResult | None:
        try:
            bridge = self._bridge_factory()
            bridge.capture_visible_screen()
            return bridge.wait_for_result(timeout_seconds=8.0)
        except Exception:
            # OCR cannot prove the recipient, so delivery must fail closed.
            return None


class ADBApprovedPrivateReplySender:
    """Type one concise reply only in a just-verified, already-open PM."""

    def __init__(
        self,
        *,
        vision: DeliveryVision | None = None,
        runner: Callable[..., Any] = subprocess.run,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._vision = vision or OcrDeliveryVision(pause=pause)
        self._runner = runner
        self._pause = pause

    def send_approved_reply(self, expected_alias: str, text: str) -> DeliveryReceipt:
        message = _valid_automatic_message(text)
        if message is None:
            return DeliveryReceipt(DeliveryState.FAILED_SAFE, "invalid_automatic_reply")
        if not _valid_alias(expected_alias):
            return DeliveryReceipt(DeliveryState.FAILED_SAFE, "invalid_approved_alias")
        preflight = self._vision.prepare_delivery(expected_alias, message)
        if not preflight.conversation_verified:
            return DeliveryReceipt(DeliveryState.FAILED_SAFE, "current_conversation_not_verified")
        if not preflight.composer_inspected:
            return DeliveryReceipt(DeliveryState.FAILED_SAFE, "composer_state_not_confirmed")
        if not preflight.composer_empty:
            # Never focus, overwrite, clear, or send a human's own draft.
            return DeliveryReceipt(DeliveryState.FAILED_SAFE, "composer_has_manual_text")
        if not self._tap(*_composer_coordinates()):
            return DeliveryReceipt(DeliveryState.FAILED_SAFE, "could_not_focus_chat_input")
        self._pause(0.15)
        if not self._input_text(message):
            # Text may now be partially present.  Do not send, erase, or retry
            # automatically: the global runtime gate will stop after this.
            return DeliveryReceipt(
                DeliveryState.FAILED_SAFE,
                "text_input_failed_before_send",
                text_entered=True,
            )
        if not self._tap(*_send_coordinates()):
            return DeliveryReceipt(
                DeliveryState.FAILED_SAFE,
                "send_tap_failed",
                text_entered=True,
            )
        if self._vision.confirms_new_outgoing_reply(
            expected_alias,
            message,
            preflight.outgoing_signature,
        ):
            return DeliveryReceipt(
                DeliveryState.CONFIRMED,
                "outgoing_bubble_confirmed_locally",
                text_entered=True,
                send_tap_attempted=True,
            )
        # A tap may already have been accepted by Last Z.  Retrying could send
        # a duplicate, so the runtime must stop its reply lane for review.
        return DeliveryReceipt(
            DeliveryState.AMBIGUOUS,
            "send_tapped_without_visual_confirmation",
            text_entered=True,
            send_tap_attempted=True,
        )

    def _tap(self, x: int, y: int) -> bool:
        return self._run(
            ADB_PATH, "-s", ADB_DEVICE, "shell", "input", "tap", str(x), str(y),
        )

    def _input_text(self, text: str) -> bool:
        for character in text:
            encoded = base64.b64encode(character.encode("utf-8")).decode("ascii")
            if not self._run(
                ADB_PATH,
                "-s",
                ADB_DEVICE,
                "shell",
                "am",
                "broadcast",
                "-a",
                "ADB_INPUT_B64",
                "--es",
                "msg",
                encoded,
            ):
                return False
            self._pause(0.008)
        return True

    def _run(self, *command: str) -> bool:
        try:
            result = self._runner(
                list(command),
                capture_output=True,
                text=False,
                timeout=5.0,
            )
        except Exception:
            return False
        return getattr(result, "returncode", 1) == 0


def _result_has_alias(result: ScreenOcrResult, expected_alias: str) -> bool:
    if is_blocking_overlay_title(result.header_text):
        return False
    header = parse_private_header(
        result.header_text,
        trusted_aliases=(expected_alias,),
        dedicated_header=True,
    )
    return header is not None and header.display_name.casefold() == expected_alias.strip().casefold()


def _valid_alias(value: str) -> bool:
    compact = " ".join(str(value or "").split())
    return 2 <= len(compact) <= 32 and any(character.isalnum() for character in compact)


def _valid_automatic_message(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    message = " ".join(value.split())
    if not message or len(message) > MAX_AUTOMATIC_REPLY_CHARS:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in message):
        return None
    return message


def _normalize_visible_text(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _composer_is_empty(value: str) -> bool:
    normalized = _normalize_visible_text(value)
    # The empty Last Z input may be recognised as its English placeholder.
    # Anything else is treated as a human draft and blocks the send lane.
    return normalized in {"", "tap to enter text"}


def _outgoing_reply_signature(result: ScreenOcrResult, text: str) -> str:
    """Return geometry of matching outgoing bubbles from one OCR result.

    Text alone cannot prove that a Send tap added a fresh bubble: an older
    identical PEva message could already be visible. Geometry makes the
    post-send proof fail closed unless the matching right-side bubble set has
    changed.
    """
    layout = result.layout
    expected = _normalize_visible_text(text)
    if layout is None or not expected:
        return ""
    lines = sorted(
        (
            line
            for line in layout.lines
            if line.left >= int(layout.width * 0.30)
        ),
        key=lambda line: (line.top, line.left),
    )
    matches: list[str] = []
    group: list[OcrLayoutLine] = []

    def flush() -> None:
        if not group:
            return
        group_lines = tuple(group)
        normalized = _normalize_visible_text(" ".join(line.text for line in group_lines))
        if expected not in normalized:
            return
        left = min(line.left for line in group_lines)
        top = min(line.top for line in group_lines)
        right = max(line.right for line in group_lines)
        bottom = max(line.bottom for line in group_lines)
        matches.append(f"{left}:{top}:{right}:{bottom}:{normalized}")

    for line in lines:
        if group:
            previous = group[-1]
            gap = line.top - previous.bottom
            same_bubble = gap <= max(42, int(layout.width * 0.05)) and abs(line.left - previous.left) <= int(layout.width * 0.22)
            if not same_bubble:
                flush()
                group = []
        group.append(line)
    flush()
    return "|".join(matches)


def _composer_coordinates() -> tuple[int, int]:
    return (int(SCREEN_WIDTH * 0.196), int(SCREEN_HEIGHT * 0.951))


def _send_coordinates() -> tuple[int, int]:
    return (int(SCREEN_WIDTH * 0.907), int(SCREEN_HEIGHT * 0.953))
