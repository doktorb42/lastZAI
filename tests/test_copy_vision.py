from __future__ import annotations

import cv2
import numpy as np

from oczy.vision import copy_menu_copy_point, find_incoming_bubbles, find_private_unread_rows


def test_finds_left_near_white_incoming_bubbles_without_reading_text() -> None:
    # Last Z-like cool beige background with two white incoming bubble shapes.
    frame = np.full((800, 500, 3), (209, 215, 221), dtype=np.uint8)
    frame[220:270, 90:300] = (240, 244, 248)
    frame[430:505, 95:360] = (240, 244, 248)
    # A right-side PEva bubble must not become an incoming candidate.
    frame[600:660, 240:450] = (190, 230, 180)

    bubbles = find_incoming_bubbles(frame)

    assert len(bubbles) == 2
    assert all(bubble.left < 150 for bubble in bubbles)
    assert [bubble.copy_point[1] for bubble in bubbles] == sorted(
        bubble.copy_point[1] for bubble in bubbles
    )


def test_rejects_light_header_and_right_side_ui() -> None:
    frame = np.full((800, 500, 3), (209, 215, 221), dtype=np.uint8)
    frame[20:110, 20:400] = (240, 244, 248)
    frame[300:380, 340:480] = (240, 244, 248)

    assert find_incoming_bubbles(frame) == ()


def test_finds_current_style_red_dot_only_in_a_private_chat_row() -> None:
    frame = np.zeros((1600, 900, 3), dtype=np.uint8)
    # Tab badge: this is intentionally above the private-chat rows.
    cv2.circle(frame, (860, 96), 11, (0, 0, 235), thickness=-1)
    # Visible unread-row badge.
    cv2.circle(frame, (835, 391), 11, (0, 0, 235), thickness=-1)

    rows = find_private_unread_rows(frame)

    assert len(rows) == 1
    assert (rows[0].center_x, rows[0].center_y) == (835, 391)


def test_rejects_red_ui_outside_the_right_side_row_area() -> None:
    frame = np.zeros((1600, 900, 3), dtype=np.uint8)
    cv2.circle(frame, (300, 391), 11, (0, 0, 235), thickness=-1)

    assert find_private_unread_rows(frame) == ()


def test_copy_point_requires_the_large_action_panel() -> None:
    frame = np.full((1600, 900, 3), 220, dtype=np.uint8)
    frame[1104:1504, :] = 45

    assert copy_menu_copy_point(frame) == (225, 1200)


def test_copy_point_rejects_a_normal_small_bottom_toolbar() -> None:
    frame = np.full((1600, 900, 3), 220, dtype=np.uint8)
    frame[1500:, :] = 45

    assert copy_menu_copy_point(frame) is None
