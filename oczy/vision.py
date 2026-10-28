"""Low-cost visual signals for Last Z private-chat reading.

This module intentionally does *not* read text. It inspects only a few visual
facts: an unread red dot in the private-chat list and the geometry of white
incoming bubbles. Exact message text is obtained later through Last Z's Copy
menu and :mod:`copy_bridge`.

It never taps the game, opens chats, sends text, or runs a background loop by
itself. A caller decides when to sample a frame (normally at 1 Hz while PEva
is awake) and must keep all navigation read-only until separately verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

from .screen_capture import capture_frame
from .screen_detection import LOADED_TEMPLATES, detect_unread_messages


@dataclass(frozen=True, slots=True)
class UnreadRow:
    """One probable private-chat row identified solely by its red unread dot."""

    center_x: int
    center_y: int
    score: float


@dataclass(frozen=True, slots=True)
class IncomingBubble:
    """A probable left-side incoming message bubble; contains no recognized text."""

    left: int
    top: int
    right: int
    bottom: int
    confidence: float

    @property
    def copy_point(self) -> tuple[int, int]:
        """A conservative point well inside the bubble for a future long-press."""
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


def copy_menu_copy_point(frame: np.ndarray) -> tuple[int, int] | None:
    """Return the fixed Copy-button point only after the action panel is visible.

    Last Z puts the Copy / Translate / Reply panel over most of the lower
    screen.  This checks that broad dark panel first; a normal chat's small
    bottom toolbar, a message bubble, or a top popup are insufficient.  It
    reads no text and makes no input by itself.
    """
    _require_color_frame(frame)
    height, width = frame.shape[:2]
    band_top, band_bottom = int(height * 0.69), int(height * 0.94)
    if band_bottom <= band_top:
        return None
    gray = cv2.cvtColor(frame[band_top:band_bottom, :], cv2.COLOR_BGR2GRAY)
    dark_coverage = float(np.mean(gray < 100))
    if dark_coverage < 0.45:
        return None
    # Center of the upper-left action button in the stable two-column panel.
    return (round(width * 0.25), round(height * 0.75))


def private_unread_signal(frame: np.ndarray) -> dict[str, float | bool]:
    """Return the existing, template-based top-level unread signal."""
    return detect_unread_messages(frame)


def find_private_unread_rows(
    frame: np.ndarray,
    *,
    threshold: float = 0.82,
) -> tuple[UnreadRow, ...]:
    """Locate visible PM-row dots with templates plus a constrained fallback.

    The fallback accepts only small red, near-round components in the right
    side of actual list rows.  It deliberately starts below the tab bar, so a
    red notification on ``Private Chat`` itself cannot become a row target.
    """
    _require_color_frame(frame)
    if not 0.5 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.5 and 1.0")
    height, width = frame.shape[:2]
    # Avoid header badges: actual private-chat rows are below the tabs and
    # their dots sit against the right portion of the list.
    x0, x1 = int(width * 0.72), int(width * 0.995)
    y0, y1 = int(height * 0.20), int(height * 0.93)
    region = frame[y0:y1, x0:x1]
    matches: list[UnreadRow] = []
    for template in LOADED_TEMPLATES.get("unread_dot", []):
        template_h, template_w = template.shape[:2]
        if template_h > region.shape[0] or template_w > region.shape[1]:
            continue
        scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        while True:
            _, score, _, position = cv2.minMaxLoc(scores)
            if score < threshold:
                break
            center_x = x0 + position[0] + template_w // 2
            center_y = y0 + position[1] + template_h // 2
            matches.append(UnreadRow(center_x, center_y, float(score)))
            _suppress(scores, position, template_w, template_h)
    matches.extend(_find_current_style_row_badges(frame, x0=x0, x1=x1, y0=y0, y1=y1))
    return _dedupe_rows(matches)


def _find_current_style_row_badges(
    frame: np.ndarray,
    *,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
) -> tuple[UnreadRow, ...]:
    """Find the current red unread-dot artwork in the already-safe row ROI."""
    height, width = frame.shape[:2]
    region = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    lower_red = cv2.inRange(hsv, (0, 120, 150), (12, 255, 255))
    upper_red = cv2.inRange(hsv, (170, 120, 150), (179, 255, 255))
    mask = cv2.bitwise_or(lower_red, upper_red)
    component_count, _labels, stats, centers = cv2.connectedComponentsWithStats(mask)

    scale = min(width / 900.0, height / 1600.0)
    min_area, max_area = 100 * scale * scale, 700 * scale * scale
    min_side, max_side = max(3.0, 11 * scale), max(8.0, 35 * scale)
    rows: list[UnreadRow] = []
    for component_index in range(1, component_count):
        _left, _top, component_width, component_height, area = stats[component_index]
        if not min_area <= area <= max_area:
            continue
        if not (min_side <= component_width <= max_side and min_side <= component_height <= max_side):
            continue
        ratio = component_width / component_height
        if not 0.55 <= ratio <= 1.6:
            continue
        center_x, center_y = centers[component_index]
        rows.append(UnreadRow(x0 + round(float(center_x)), y0 + round(float(center_y)), 0.90))
    return tuple(rows)


def find_incoming_bubbles(frame: np.ndarray) -> tuple[IncomingBubble, ...]:
    """Find likely white, left-side Last Z message bubbles without OCR.

    The UI background is cool beige (value around 220), while an incoming
    bubble is near-white (value around 248). A strict brightness threshold,
    connected-component geometry and a left-side rule keep this deliberately
    conservative. Ambiguous frames should produce no candidates rather than
    a guessed copy target.
    """
    _require_color_frame(frame)
    height, width = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 235), (180, 55, 255))
    mask[: int(height * 0.14), :] = 0
    mask[int(height * 0.93):, :] = 0
    mask[:, int(width * 0.68):] = 0

    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(5, width // 85), max(3, height // 150)),
    )
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    bubbles: list[IncomingBubble] = []
    for contour in contours:
        left, top, bubble_width, bubble_height = cv2.boundingRect(contour)
        right, bottom = left + bubble_width, top + bubble_height
        if not _looks_like_incoming_bubble(
                left, top, right, bottom, width=width, height=height,
        ):
            continue
        area = bubble_width * bubble_height
        confidence = min(1.0, 0.65 + area / float(width * height) * 4.0)
        bubbles.append(IncomingBubble(left, top, right, bottom, confidence))
    return tuple(sorted(bubbles, key=lambda item: (item.top, item.left)))


def _looks_like_incoming_bubble(
    left: int,
    top: int,
    right: int,
    bottom: int,
    *,
    width: int,
    height: int,
) -> bool:
    bubble_width, bubble_height = right - left, bottom - top
    return (
        int(width * 0.08) <= bubble_width <= int(width * 0.58)
        and int(height * 0.025) <= bubble_height <= int(height * 0.22)
        and left <= int(width * 0.30)
        and right <= int(width * 0.76)
        and int(height * 0.14) <= top < bottom <= int(height * 0.93)
    )


def _suppress(scores: np.ndarray, position: tuple[int, int], width: int, height: int) -> None:
    x, y = position
    x0, x1 = max(0, x - width // 2), min(scores.shape[1], x + width // 2 + 1)
    y0, y1 = max(0, y - height // 2), min(scores.shape[0], y + height // 2 + 1)
    scores[y0:y1, x0:x1] = -1.0


def _dedupe_rows(rows: Iterable[UnreadRow]) -> tuple[UnreadRow, ...]:
    kept: list[UnreadRow] = []
    for row in sorted(rows, key=lambda item: item.score, reverse=True):
        if any(abs(row.center_y - prior.center_y) < 32 for prior in kept):
            continue
        kept.append(row)
    return tuple(sorted(kept, key=lambda item: item.center_y))


def _require_color_frame(frame: np.ndarray) -> None:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("frame must be a BGR image with three channels")
