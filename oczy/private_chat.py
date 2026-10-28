"""Read-only, low-power visual observation for Last Z private chats.

The observer has no game-control methods. It samples a single frame on demand,
then reports whether a private-chat event is visible and where a future,
separately-authorized Copy reader could look. This separation prevents an
uncertain visual match from ever becoming a game action.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .vision import (
    IncomingBubble,
    UnreadRow,
    capture_frame,
    find_incoming_bubbles,
    find_private_unread_rows,
    private_unread_signal,
)
from .screen_detection import classify_screen, get_scores


@dataclass(frozen=True, slots=True)
class PrivateChatObservation:
    captured_at: float
    screen: str
    screen_confidence: float
    private_unread: bool
    private_unread_score: float
    unread_rows: tuple[UnreadRow, ...]
    incoming_bubbles: tuple[IncomingBubble, ...]
    # True only when the red badge was seen in the left half of the lower
    # Private/Group chooser. The shared global top badge is never a trigger.
    private_tab_badge: bool = False

    @property
    def should_prepare_copy(self) -> bool:
        """True only for a direct PM signal or a visible PM-row signal."""
        return self.private_tab_badge or bool(self.unread_rows)

    @property
    def is_private_conversation(self) -> bool:
        return self.screen == "PRIVATE_CONVERSATION"


class PrivateChatEyes:
    """Single-frame observer. Schedule it at 1 Hz; it never runs itself."""

    recommended_interval_seconds = 1.0

    def observe_once(self) -> PrivateChatObservation:
        return self.observe_frame(capture_frame())

    def observe_frame(self, frame: np.ndarray) -> PrivateChatObservation:
        classification = classify_screen(get_scores(frame))
        unread = private_unread_signal(frame)
        screen = str(classification["screen"])
        rows = (
            find_private_unread_rows(frame)
            if screen == "PRIVATE_CHAT"
            else ()
        )
        bubbles = (
            find_incoming_bubbles(frame)
            if screen == "PRIVATE_CONVERSATION"
            else ()
        )
        return PrivateChatObservation(
            captured_at=time.time(),
            screen=screen,
            screen_confidence=float(classification["confidence"]),
            private_unread=bool(unread["private"]),
            private_unread_score=float(unread["private_score"]),
            unread_rows=rows,
            incoming_bubbles=bubbles,
            private_tab_badge=bool(unread["private_badge"]),
        )
