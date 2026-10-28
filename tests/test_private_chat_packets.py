from __future__ import annotations

from oczy.vision import IncomingBubble
from oczy.private_chat import PrivateChatObservation
from oczy.private_reader import PrivateChatReadEvent
from oczy.packets import (
    ChatPacket,
    PacketBurstBuffer,
    PrivateChatPacketReader,
    merge_ocr_pages,
)
from oczy.local_ocr import ScreenOcrResult


def _conversation(*bubbles: IncomingBubble) -> PrivateChatObservation:
    return PrivateChatObservation(1.0, "PRIVATE_CONVERSATION", 0.99, False, 0.0, (), bubbles)


class FakeEyes:
    def __init__(self, *observations: PrivateChatObservation) -> None:
        self.observations = iter(observations)

    def observe_once(self) -> PrivateChatObservation:
        return next(self.observations)


class FakeOcr:
    def __init__(self, *results: ScreenOcrResult) -> None:
        self.results = iter(results)
        self.capture_count = 0

    def capture_visible_screen(self) -> None:
        self.capture_count += 1

    def wait_for_result(self, *, timeout_seconds: float = 8.0) -> ScreenOcrResult:
        assert timeout_seconds == 8.0
        return next(self.results)


class FakeCoordinator:
    def __init__(self, source: PrivateChatReadEvent, eyes: FakeEyes, ocr: FakeOcr) -> None:
        self.source = source
        self.eyes = eyes
        self.ocr = ocr

    def open_single_unread_and_read(self) -> PrivateChatReadEvent:
        return self.source


class FakeScroller:
    def __init__(self) -> None:
        self.moves: list[str] = []

    def show_older_messages(self) -> bool:
        self.moves.append("older")
        return True

    def return_to_newest_messages(self) -> bool:
        self.moves.append("newest")
        return True


def test_clipped_message_collects_one_older_page_and_returns_to_newest() -> None:
    clipped = _conversation(IncomingBubble(80, 100, 340, 400, 0.9))
    older_page = _conversation(IncomingBubble(80, 420, 340, 520, 0.9))
    first = ScreenOcrResult("done", "shared one\nshared two\nlatest", 10)
    source = PrivateChatReadEvent("new_text", clipped, first)
    scroller = FakeScroller()
    reader = PrivateChatPacketReader(
        coordinator=FakeCoordinator(
            source,
            FakeEyes(older_page),
            FakeOcr(ScreenOcrResult("done", "older\nshared one\nshared two", 11)),
        ),
        scroller=scroller,
        max_extra_pages=2,
    )

    event = reader.poll_once()

    assert event.kind == "packet_read"
    assert event.packet is not None
    assert event.packet.text == "older\nshared one\nshared two\nlatest"
    assert event.packet.page_count == 2
    assert event.packet.complete
    assert scroller.moves == ["older", "newest"]


def test_nontext_message_is_not_turned_into_a_guessed_reply() -> None:
    observation = _conversation(IncomingBubble(80, 360, 340, 500, 0.9))
    source = PrivateChatReadEvent("empty_text", observation, ScreenOcrResult("done", "", 10))
    reader = PrivateChatPacketReader(
        coordinator=FakeCoordinator(source, FakeEyes(), FakeOcr()),
        scroller=FakeScroller(),
    )

    event = reader.poll_once()

    assert event.kind == "nontext_or_unreadable"
    assert event.packet is not None
    assert event.packet.text == ""
    assert event.packet.contains_nontext_signal


def test_profile_overlay_never_becomes_a_packet() -> None:
    observation = _conversation(IncomingBubble(80, 360, 340, 500, 0.9))
    source = PrivateChatReadEvent(
        "overlay_open",
        observation,
        ScreenOcrResult("done", "background text", 10, "Survivor Profile"),
    )
    reader = PrivateChatPacketReader(
        coordinator=FakeCoordinator(source, FakeEyes(), FakeOcr()),
        scroller=FakeScroller(),
    )

    event = reader.poll_once()

    assert event.kind == "overlay_open"
    assert event.packet is None


def test_packet_burst_waits_for_quiet_then_keeps_two_distinct_ok_replies() -> None:
    buffer = PacketBurstBuffer(quiet_seconds=1.0, maximum_seconds=4.0)
    buffer.add(ChatPacket("OK", 1, True, "visible_message_complete", 1), now=0.0)
    buffer.add(ChatPacket("OK", 1, True, "visible_message_complete", 2), now=0.7)

    assert not buffer.ready(now=1.5)
    assert buffer.ready(now=1.8)
    packet = buffer.take()

    assert packet is not None
    assert packet.text == "OK\nOK"
    assert packet.page_count == 2


def test_packet_burst_can_be_discarded_when_a_modal_covers_the_chat() -> None:
    buffer = PacketBurstBuffer(quiet_seconds=1.0, maximum_seconds=4.0)
    buffer.add(ChatPacket("hello", 1, True, "visible_message_complete", 1), now=0.0)

    buffer.discard()

    assert not buffer.pending
    assert buffer.take() is None


def test_merge_removes_only_clear_multi_line_page_overlap() -> None:
    assert merge_ocr_pages(("older\nshared one\nshared two", "shared one\nshared two\nlatest")) == (
        "older\nshared one\nshared two\nlatest"
    )
