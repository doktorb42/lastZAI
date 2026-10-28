"""Najwęższa część Rąk potrzebna Oczom do odczytu prywatnego czatu.

Ten moduł potrafi wyłącznie otworzyć środek jednego, już wizualnie
potwierdzonego wiersza PM. Nie zna tekstu rozmowy, pola wpisywania, przycisku
wysyłania ani żadnej akcji w grze. Decyzja *czy* wolno wykonać gest pozostaje
w Oczach; Ręce znają tylko bezpiecznie przeliczone współrzędne.
"""

from __future__ import annotations

import time
from collections.abc import Callable


Tap = Callable[[int, int], bool]
Swipe = Callable[[int, int, int, int, int], bool]
Sleeper = Callable[[float], None]


def refresh_verified_private_chat_list(
    *,
    screen_width: int,
    screen_height: int,
    tapper: Tap | None = None,
    sleeper: Sleeper = time.sleep,
) -> bool:
    """Refresh a visible PM list through two fixed, non-destructive tabs.

    Last Z sometimes paints the top PM badge before it repaints the unread
    dots inside the list.  This gesture may only be requested after Oczy has
    already confirmed the ``PRIVATE_CHAT`` list and a long quiet period.  It
    taps *Friend Post*, then returns to *Private Chat*; it never opens a row,
    touches a composer, or knows any message text.
    """
    width, height = int(screen_width), int(screen_height)
    if width < 700 or height < 1_100:
        return False
    # Last Z's upper chat strip: Friend Post is just left of Private Chat.
    # These ratios remain inside the tab interiors at both 900x1600 and the
    # scaled BlueStacks portrait viewport, far above every chat control.
    friend_post = (round(width * 0.67), round(height * 0.06))
    private_chat = (round(width * 0.90), round(height * 0.06))
    if tapper is None:
        try:
            from .read_only_adb import tap as adb_tap
        except Exception:
            return False
        tapper = adb_tap
    try:
        if not bool(tapper(*friend_post)):
            return False
        # A brief settle is required for Last Z to repaint the tab content.
        sleeper(0.35)
        return bool(tapper(*private_chat))
    except Exception:
        return False


def open_verified_private_chat_row(
    *,
    row_center_x: int,
    row_center_y: int,
    tapper: Tap | None = None,
) -> bool:
    """Tap the content of one confirmed PM row, never a badge or chat input."""
    center_x = int(row_center_x)
    center_y = int(row_center_y)
    if center_x <= 0 or center_y <= 0:
        return False
    # The unread marker lies close to the right edge.  The leftward content
    # point is intentional: it cannot land on the marker, the text input or
    # the Send button, all of which live in different screen regions.
    content_x = max(100, int(round(center_x * 0.48)))
    if tapper is None:
        try:
            from .read_only_adb import tap as adb_tap
        except Exception:
            return False
        tapper = adb_tap
    try:
        return bool(tapper(content_x, center_y))
    except Exception:
        return False


def scroll_verified_private_chat_history(
    *,
    screen_width: int,
    screen_height: int,
    toward_older: bool,
    swiper: Swipe | None = None,
) -> bool:
    """Perform one central, bounded history swipe in an open private chat.

    It is intentionally not a generic gesture API: the only allowed movement
    stays in the middle of the conversation pane and cannot touch the composer
    or Send button. Callers use it only after visual proof that a clipped PM
    bubble is visible.
    """
    width = int(screen_width)
    height = int(screen_height)
    if width < 200 or height < 400:
        return False
    x = width // 2
    upper = int(height * 0.36)
    lower = int(height * 0.73)
    y1, y2 = (upper, lower) if toward_older else (lower, upper)
    if swiper is None:
        try:
            from .read_only_adb import swipe as adb_swipe
        except Exception:
            return False
        swiper = adb_swipe
    try:
        return bool(swiper(x, y1, x, y2, 260))
    except Exception:
        return False
