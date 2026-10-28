from __future__ import annotations

import time

from oczy.vision import IncomingBubble
from oczy.eyes_window import EyesWatchWorker
from oczy.private_chat import PrivateChatObservation
from oczy.private_reader import PrivateChatReadEvent


class FakeOcr:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeCoordinator:
    def __init__(self) -> None:
        self.ocr = FakeOcr()
        self.calls = 0

    def open_single_unread_and_read(self) -> PrivateChatReadEvent:
        self.calls += 1
        observation = PrivateChatObservation(
            1.0,
            "PRIVATE_CONVERSATION",
            0.99,
            False,
            0.0,
            (),
            (IncomingBubble(100, 300, 260, 360, 0.9),),
        )
        return PrivateChatReadEvent("idle", observation)


def test_ui_worker_stops_the_android_helper() -> None:
    coordinator = FakeCoordinator()
    worker = EyesWatchWorker(coordinator_factory=lambda: coordinator, interval_seconds=0.01)

    assert worker.start()
    deadline = time.monotonic() + 1.0
    while coordinator.calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.request_stop()
    deadline = time.monotonic() + 1.0
    while worker.is_running and time.monotonic() < deadline:
        time.sleep(0.01)

    assert coordinator.calls > 0
    assert coordinator.ocr.stopped
