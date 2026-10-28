"""Ręce mogą tylko otworzyć potwierdzony wiersz PM; nie znają czatu ani tekstu."""

from __future__ import annotations

import unittest

from rece import (
    open_verified_private_chat_row,
    refresh_verified_private_chat_list,
    scroll_verified_private_chat_history,
)


class ReadOnlyNavigationTests(unittest.TestCase):
    def test_row_tap_targets_content_left_of_badge(self) -> None:
        calls: list[tuple[int, int]] = []

        result = open_verified_private_chat_row(
            row_center_x=1000,
            row_center_y=420,
            tapper=lambda x, y: calls.append((x, y)) or True,
        )

        self.assertTrue(result)
        self.assertEqual(calls, [(480, 420)])

    def test_invalid_geometry_never_taps(self) -> None:
        calls: list[tuple[int, int]] = []

        result = open_verified_private_chat_row(
            row_center_x=0,
            row_center_y=420,
            tapper=lambda x, y: calls.append((x, y)) or True,
        )

        self.assertFalse(result)
        self.assertEqual(calls, [])

    def test_tapper_failure_returns_false(self) -> None:
        result = open_verified_private_chat_row(
            row_center_x=1000,
            row_center_y=420,
            tapper=lambda _x, _y: (_ for _ in ()).throw(RuntimeError("ADB unavailable")),
        )

        self.assertFalse(result)

    def test_history_swipe_stays_in_conversation_centre(self) -> None:
        calls: list[tuple[int, int, int, int, int]] = []

        result = scroll_verified_private_chat_history(
            screen_width=1080,
            screen_height=1920,
            toward_older=True,
            swiper=lambda x1, y1, x2, y2, duration: calls.append((x1, y1, x2, y2, duration)) or True,
        )

        self.assertTrue(result)
        self.assertEqual(calls, [(540, 691, 540, 1401, 260)])

    def test_invalid_screen_geometry_never_swipes(self) -> None:
        calls: list[tuple[int, int, int, int, int]] = []

        result = scroll_verified_private_chat_history(
            screen_width=100,
            screen_height=1920,
            toward_older=False,
            swiper=lambda x1, y1, x2, y2, duration: calls.append((x1, y1, x2, y2, duration)) or True,
        )

        self.assertFalse(result)
        self.assertEqual(calls, [])

    def test_private_list_refresh_visits_only_friend_post_then_private_tab(self) -> None:
        calls: list[tuple[int, int]] = []
        waits: list[float] = []

        result = refresh_verified_private_chat_list(
            screen_width=900,
            screen_height=1600,
            tapper=lambda x, y: calls.append((x, y)) or True,
            sleeper=waits.append,
        )

        self.assertTrue(result)
        self.assertEqual(calls, [(603, 96), (810, 96)])
        self.assertEqual(waits, [0.35])

    def test_private_list_refresh_fails_before_second_tap_when_first_tab_is_unavailable(self) -> None:
        calls: list[tuple[int, int]] = []

        result = refresh_verified_private_chat_list(
            screen_width=900,
            screen_height=1600,
            tapper=lambda x, y: calls.append((x, y)) or False,
            sleeper=lambda _seconds: self.fail("must not wait after a failed tap"),
        )

        self.assertFalse(result)
        self.assertEqual(calls, [(603, 96)])


if __name__ == "__main__":
    unittest.main()
