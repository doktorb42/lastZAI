import cv2
import numpy as np

from oczy.screen_detection import PRIVATE_UNREAD_POS, has_private_unread_badge


def test_current_style_private_badge_is_detected_at_lower_private_subtab() -> None:
    image = np.zeros((1600, 900, 3), dtype=np.uint8)
    cv2.circle(image, PRIVATE_UNREAD_POS, 11, (0, 0, 235), thickness=-1)

    assert has_private_unread_badge(image)


def test_red_shape_elsewhere_is_not_private_unread_badge() -> None:
    image = np.zeros((1600, 900, 3), dtype=np.uint8)
    cv2.circle(image, (120, 500), 11, (0, 0, 235), thickness=-1)

    assert not has_private_unread_badge(image)


def test_global_group_or_alliance_badge_cannot_become_a_private_unread_badge() -> None:
    image = np.zeros((1600, 900, 3), dtype=np.uint8)
    # A global-tab dot is ambiguous and must never be a PM trigger.
    cv2.circle(image, (620, 80), 11, (0, 0, 235), thickness=-1)

    assert not has_private_unread_badge(image)


def test_current_top_edge_global_badge_is_ignored() -> None:
    image = np.zeros((1600, 900, 3), dtype=np.uint8)
    # Last Z shows this upper badge for both Group Chat and PM activity.
    cv2.circle(image, (850, 22), 11, (0, 0, 235), thickness=-1)

    assert not has_private_unread_badge(image)


def test_lower_group_chat_badge_is_ignored() -> None:
    image = np.zeros((1600, 900, 3), dtype=np.uint8)
    # Lower Group Chat is the right half of the Private/Group chooser.
    cv2.circle(image, (790, 350), 11, (0, 0, 235), thickness=-1)

    assert not has_private_unread_badge(image)


def test_oversized_red_panel_is_not_private_unread_badge() -> None:
    image = np.zeros((1600, 900, 3), dtype=np.uint8)
    cv2.rectangle(image, (830, 70), (890, 130), (0, 0, 235), thickness=-1)

    assert not has_private_unread_badge(image)
