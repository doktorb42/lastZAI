from __future__ import annotations

from oczy.vision import IncomingBubble
from oczy.watch import describe_event, run_watch
from oczy.private_chat import PrivateChatObservation
from oczy.private_reader import PrivateChatReadEvent
from oczy.local_ocr import ScreenOcrResult


def _observation() -> PrivateChatObservation:
    return PrivateChatObservation(
        1.0,
        "PRIVATE_CONVERSATION",
        0.99,
        False,
        0.0,
        (),
        (IncomingBubble(100, 300, 260, 360, 0.9),),
    )


class FakeOcr:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeCoordinator:
    def __init__(self, *events: PrivateChatReadEvent) -> None:
        self.events = iter(events)
        self.ocr = FakeOcr()

    def open_single_unread_and_read(self) -> PrivateChatReadEvent:
        return next(self.events)


def test_describe_event_never_includes_recognized_chat_text() -> None:
    event = PrivateChatReadEvent(
        "new_text",
        _observation(),
        ScreenOcrResult("done", "prywatna wiadomosc", 42),
    )

    line = describe_event(event)

    assert line is not None
    assert "prywatna wiadomosc" not in line
    assert "18" in line


def test_run_watch_stops_the_android_helper_after_a_bounded_test() -> None:
    coordinator = FakeCoordinator(PrivateChatReadEvent("idle", _observation()))
    output: list[str] = []

    run_watch(
        coordinator=coordinator,
        interval_seconds=0.5,
        max_cycles=1,
        sleeper=lambda _: None,
        output=output.append,
    )

    assert coordinator.ocr.stopped
    assert output[-1] == "PEva nie czuwa. Androidowy pomocnik zostal zatrzymany."
