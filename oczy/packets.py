"""Bounded, in-memory packet reading for Last Z private conversations.

This is the layer between visual detection and any future reply decision.  It
collects a short burst of text safely, but never starts a model, types, sends,
or writes message text to disk.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, Sequence

from .private_chat import PrivateChatObservation
from .private_reader import PrivateChatOcrCoordinator, PrivateChatReadEvent
from .identity import is_blocking_overlay_title, parse_private_header
from config.runtime_config import SCREEN_HEIGHT, SCREEN_WIDTH
from .local_ocr import ScreenOcrLayout, ScreenOcrResult


# A normal human often sends "hey" and the actual question as two separate
# bubbles. Two seconds is short enough for chat, but avoids replying into the
# middle of that common burst. The hard maximum keeps a longer stream bounded.
QUIET_SECONDS = 2.0
MAX_BURST_SECONDS = 6.0
MAX_EXTRA_PAGES = 2
MAX_COLLECTION_SECONDS = 8.0


class ConversationScroller(Protocol):
    """Minimal input surface for a bounded conversation-only history read."""

    def show_older_messages(self) -> bool: ...

    def return_to_newest_messages(self) -> bool: ...


class ADBConversationScroller:
    """Short centre-of-chat swipes, never taps, types, or sends."""

    def show_older_messages(self) -> bool:
        # Finger down moves conversation history down and exposes older text.
        return self._swipe(SCREEN_WIDTH // 2, int(SCREEN_HEIGHT * 0.36), SCREEN_WIDTH // 2, int(SCREEN_HEIGHT * 0.73))

    def return_to_newest_messages(self) -> bool:
        return self._swipe(SCREEN_WIDTH // 2, int(SCREEN_HEIGHT * 0.73), SCREEN_WIDTH // 2, int(SCREEN_HEIGHT * 0.36))

    @staticmethod
    def _swipe(x1: int, y1: int, x2: int, y2: int) -> bool:
        from rece import scroll_verified_private_chat_history

        # The protocol only requests these two known directions.  Refuse a
        # changed geometry rather than turning Oczy into a general gesture
        # executor.
        expected_x = SCREEN_WIDTH // 2
        upper = int(SCREEN_HEIGHT * 0.36)
        lower = int(SCREEN_HEIGHT * 0.73)
        if (x1, x2) != (expected_x, expected_x):
            return False
        if (y1, y2) == (upper, lower):
            toward_older = True
        elif (y1, y2) == (lower, upper):
            toward_older = False
        else:
            return False
        return scroll_verified_private_chat_history(
            screen_width=SCREEN_WIDTH,
            screen_height=SCREEN_HEIGHT,
            toward_older=toward_older,
        )


@dataclass(frozen=True, slots=True)
class ChatPacket:
    """A transient packet ready for a future policy/model decision."""

    text: str
    page_count: int
    complete: bool
    stop_reason: str
    captured_at_ms: int
    contains_nontext_signal: bool = False
    # A name is useful only when it came from the dedicated PM header,
    # never from a guessed line in the conversation body.
    conversation_header: str = ""
    # Newer Android companions include line geometry from this same one-shot
    # read. It is transient and optional for compatibility with older builds.
    layout: ScreenOcrLayout | None = None


@dataclass(frozen=True, slots=True)
class PacketReadEvent:
    """Packet-reader result, always retaining the original safe observation."""

    kind: str
    source: PrivateChatReadEvent
    packet: ChatPacket | None = None
    detail: str = ""


class PrivateChatPacketReader:
    """Collect a current message and, only when visibly clipped, extra pages."""

    def __init__(
        self,
        *,
        coordinator: PrivateChatOcrCoordinator | None = None,
        scroller: ConversationScroller | None = None,
        max_extra_pages: int = MAX_EXTRA_PAGES,
        max_collection_seconds: float = MAX_COLLECTION_SECONDS,
    ) -> None:
        if not 0 <= int(max_extra_pages) <= 4:
            raise ValueError("max_extra_pages must be between 0 and 4")
        if not 2.0 <= float(max_collection_seconds) <= 15.0:
            raise ValueError("max_collection_seconds must be between 2 and 15")
        self.coordinator = coordinator or PrivateChatOcrCoordinator()
        self.scroller = scroller or ADBConversationScroller()
        self.max_extra_pages = int(max_extra_pages)
        self.max_collection_seconds = float(max_collection_seconds)

    def poll_once(self) -> PacketReadEvent:
        """Read one newly seen screen and turn it into a bounded packet."""
        source = self.coordinator.open_single_unread_and_read()
        if source.kind == "empty_text" and source.observation.is_private_conversation:
            packet = ChatPacket(
                "", 1, True, "nontext_or_unreadable", _captured_at(source.result), True,
            )
            return PacketReadEvent(
                "nontext_or_unreadable",
                source,
                packet,
                "Widać nowy element rozmowy, ale nie ma pewnego tekstu do odczytania.",
            )
        if source.kind == "overlay_open":
            # Keep the specific reason for the runtime.  It must be able to
            # discard a pending burst rather than replying after a profile
            # window has hidden the conversation.
            return PacketReadEvent("overlay_open", source, detail=source.detail)
        if source.kind != "new_text" or source.result is None:
            return PacketReadEvent("no_packet", source, detail=source.detail)
        return self._collect_clipped_message(source)

    def _collect_clipped_message(self, source: PrivateChatReadEvent) -> PacketReadEvent:
        first_result = source.result
        pages_newest_to_oldest = [first_result.text]
        current = source.observation
        moved_pages = 0
        restore_newest = True
        complete = True
        stop_reason = "visible_message_complete"
        deadline = time.monotonic() + self.max_collection_seconds

        try:
            while (
                moved_pages < self.max_extra_pages
                and _message_may_continue_above(current)
                and time.monotonic() < deadline
            ):
                if not current.is_private_conversation:
                    complete = False
                    stop_reason = "event_or_unknown_screen"
                    break
                if not self.scroller.show_older_messages():
                    complete = False
                    stop_reason = "history_swipe_failed"
                    break
                moved_pages += 1
                time.sleep(0.35)
                current = self.coordinator.eyes.observe_once()
                if not current.is_private_conversation:
                    complete = False
                    stop_reason = "event_or_unknown_screen"
                    break
                if not current.incoming_bubbles:
                    complete = False
                    stop_reason = "no_incoming_bubble_after_scroll"
                    break
                try:
                    self.coordinator.ocr.capture_visible_screen()
                    result = self.coordinator.ocr.wait_for_result(timeout_seconds=8.0)
                except Exception:
                    complete = False
                    stop_reason = "additional_ocr_failed"
                    break
                if is_blocking_overlay_title(result.header_text):
                    # The newest view is no longer reliably a conversation.
                    # Do not return a partial packet whose attribution could
                    # be wrong after a profile/modal opened mid-collection.
                    restore_newest = False
                    return PacketReadEvent(
                        "overlay_open",
                        source,
                        detail="Okno profilu zasłoniło rozmowę podczas odczytu.",
                    )
                normalized = _normalize_page(result.text)
                if not normalized:
                    complete = False
                    stop_reason = "nontext_or_unreadable"
                    break
                pages_newest_to_oldest.append(normalized)
                if not _message_may_continue_above(current):
                    break
            else:
                if moved_pages >= self.max_extra_pages and _message_may_continue_above(current):
                    complete = False
                    stop_reason = "page_limit_reached"
                elif time.monotonic() >= deadline:
                    complete = False
                    stop_reason = "time_limit_reached"
        finally:
            # Restore the newest message only while the chat is still known.
            # If an event/popup took over, fail closed and leave the UI alone.
            if moved_pages and restore_newest and current.is_private_conversation:
                restored = True
                for _ in range(moved_pages):
                    if not self.scroller.return_to_newest_messages():
                        restored = False
                if not restored:
                    complete = False
                    stop_reason = "newest_view_not_restored"

        merged = merge_ocr_pages(tuple(reversed(pages_newest_to_oldest)))
        packet = ChatPacket(
            text=merged,
            page_count=len(pages_newest_to_oldest),
            complete=complete,
            stop_reason=stop_reason,
            captured_at_ms=first_result.captured_at_ms,
            contains_nontext_signal=not bool(merged),
            conversation_header=first_result.header_text,
            layout=first_result.layout,
        )
        return PacketReadEvent("packet_read", source, packet, stop_reason)


@dataclass(slots=True)
class PacketBurstBuffer:
    """Wait briefly for a human's message burst before a reply is considered."""

    quiet_seconds: float = QUIET_SECONDS
    maximum_seconds: float = MAX_BURST_SECONDS
    _packets: list[ChatPacket] = field(default_factory=list, init=False, repr=False)
    _first_seen: float | None = field(default=None, init=False, repr=False)
    _last_seen: float | None = field(default=None, init=False, repr=False)
    _conversation_key: tuple[str | None, str] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.quiet_seconds <= 0 or self.maximum_seconds < self.quiet_seconds:
            raise ValueError("invalid packet burst timing")

    @property
    def pending(self) -> bool:
        return bool(self._packets)

    def add(self, packet: ChatPacket, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        conversation_key = _conversation_key(packet.conversation_header)
        # Never attach text to an identity observed on a different screen.  A
        # missing/invalid header is also a boundary: the packet remains useful
        # as an OCR observation, but must not inherit the preceding player's
        # identity.  This loses at most an unfinished burst, never attributes
        # a message to the wrong player after a chat switch or OCR failure.
        if self._packets and conversation_key != self._conversation_key:
            self._reset()
        self._packets.append(packet)
        if self._first_seen is None:
            self._first_seen = current
        self._last_seen = current
        self._conversation_key = conversation_key

    def ready(self, *, now: float | None = None) -> bool:
        if self._first_seen is None or self._last_seen is None:
            return False
        current = time.monotonic() if now is None else now
        return (
            current - self._last_seen >= self.quiet_seconds
            or current - self._first_seen >= self.maximum_seconds
        )

    def take(self) -> ChatPacket | None:
        if not self._packets:
            return None
        packets = tuple(self._packets)
        self._reset()
        merged = merge_ocr_pages(tuple(packet.text for packet in packets))
        return ChatPacket(
            text=merged,
            page_count=sum(packet.page_count for packet in packets),
            complete=all(packet.complete for packet in packets),
            stop_reason="burst_complete",
            captured_at_ms=packets[-1].captured_at_ms,
            contains_nontext_signal=any(packet.contains_nontext_signal for packet in packets),
            conversation_header=packets[-1].conversation_header,
            layout=packets[-1].layout,
        )

    def discard(self) -> None:
        """Forget an unfinished burst after the conversation is obscured."""
        self._reset()

    def _reset(self) -> None:
        self._packets.clear()
        self._first_seen = None
        self._last_seen = None
        self._conversation_key = None


def merge_ocr_pages(pages: Sequence[str]) -> str:
    """Join page overlaps without deleting separate, identical chat replies."""
    merged: list[str] = []
    for page in pages:
        lines = _page_lines(page)
        if not lines:
            continue
        overlap = _tail_head_overlap(merged, lines)
        merged.extend(lines[overlap:])
    return "\n".join(merged)


def _page_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def _normalize_page(text: str) -> str:
    return "\n".join(_page_lines(text))


def _tail_head_overlap(existing: Sequence[str], incoming: Sequence[str]) -> int:
    maximum = min(len(existing), len(incoming), 24)
    # One-line matches are too ambiguous: two separate "OK" replies must not
    # disappear merely because they occupy adjacent viewports.
    for size in range(maximum, 1, -1):
        if [line.casefold() for line in existing[-size:]] == [line.casefold() for line in incoming[:size]]:
            return size
    return 0


def _conversation_key(header_text: str) -> tuple[str | None, str] | None:
    """Return a stable safe key only from a valid dedicated PM header."""
    header = parse_private_header(header_text, dedicated_header=True)
    if header is None:
        return None
    return (header.alliance, header.display_name.casefold())


def _message_may_continue_above(observation: PrivateChatObservation) -> bool:
    """Require a tall incoming bubble actually touching the protected header."""
    header_edge = int(SCREEN_HEIGHT * 0.18)
    minimum_height = int(SCREEN_HEIGHT * 0.075)
    return any(
        bubble.top <= header_edge and bubble.bottom - bubble.top >= minimum_height
        for bubble in observation.incoming_bubbles
    )


def _captured_at(result: ScreenOcrResult | None) -> int:
    return result.captured_at_ms if result is not None else int(time.time() * 1000)
