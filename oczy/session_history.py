"""Short-lived, structured context for the currently visible private chat.

This module deliberately keeps no database and never treats a displayed name
as a durable player identity.  Its only job is to retain a few verified
left/right chat turns while this PEva process is running.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re
from typing import Iterable, Literal

from .identity import is_last_z_sender_header, parse_private_header
from .local_ocr import OcrLayoutLine, ScreenOcrLayout


TurnRole = Literal["user", "assistant"]
_TIME_OR_DATE = re.compile(r"^(?:\d{1,2}:\d{2}|\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?)$")
_CHAT_UI = re.compile(
    r"^(?:reply\b|private\s+chat$|group\s+chat$|tap\s+to\s+enter\s+text$|you\s+got\b)",
    re.IGNORECASE,
)
_REPLY_MARKER = re.compile(r"^reply\s+[^:]{1,64}:", re.IGNORECASE)
_SYSTEM_CARD = re.compile(
    r"^(?:headquarters|give\s+gifts|lucky\s+number(?:\s*x?\d+)?|"
    r"(?:lv\.?\s*\d+\s*)?\[[^\]]{1,24}\]\s*state\b|"
    r"state\b|(?:\d+\s+)?x\s*:\s*\d+\s+y\s*:\s*\d+\]?)$",
    re.IGNORECASE,
)
# Last Z lets players send animated, pre-written chat emotes.  They look like
# ordinary incoming bubbles to the visual detector, but their illustrated
# character and stock wording are not a conversational turn for PEva.  Match
# only distinctive phrases, never broad game words such as "fire" alone.
_ANIMATED_EMOTE = re.compile(
    r"(?:fire\s+buddy(?:'s|s)?\s+on\s+the\s+job|"
    r"flame\s+tamer|fight\s+your\s+flames)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """A line group whose side was verified from OCR geometry."""

    role: TurnRole
    content: str


@dataclass(frozen=True, slots=True)
class SessionView:
    """The one new player message and its immediately preceding turns."""

    incoming_text: str
    history: tuple[ConversationTurn, ...]


class SessionHistory:
    """Bounded, RAM-only history indexed by an exact temporary PM header."""

    def __init__(self, *, max_sessions: int = 4, max_turns: int = 10) -> None:
        if not 1 <= int(max_sessions) <= 16:
            raise ValueError("max_sessions must be between 1 and 16")
        if not 2 <= int(max_turns) <= 24:
            raise ValueError("max_turns must be between 2 and 24")
        self.max_sessions = int(max_sessions)
        self.max_turns = int(max_turns)
        self._sessions: OrderedDict[str, tuple[ConversationTurn, ...]] = OrderedDict()

    def observe(
        self,
        header_text: str,
        layout: ScreenOcrLayout,
        *,
        trusted_aliases: Iterable[str] = (),
    ) -> SessionView | None:
        """Merge one visible transcript and return its newest incoming turn.

        A nickname change simply starts a new, empty RAM session.  That is
        intentionally safer than trying to reconnect a person by similar text.
        """
        header = parse_private_header(
            header_text,
            trusted_aliases=trusted_aliases,
            dedicated_header=True,
        )
        if header is None:
            return None
        # Do not fall back to an older visible player turn when the newest
        # thing on screen is an illustrated, stock Last Z emote.  Otherwise a
        # player could send an animation after "hello" and PEva would greet
        # them again as if the animation did not exist.
        if has_trailing_animated_emote(layout):
            return None
        visible = extract_visible_turns(layout)
        if not visible or visible[-1].role != "user":
            return None
        key = f"{header.alliance or ''}|{header.display_name.casefold()}"
        prior = self._sessions.pop(key, ())
        merged = _merge_turns(prior, visible, limit=self.max_turns)
        self._sessions[key] = merged
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
        latest = merged[-1]
        if latest.role != "user":
            return None
        return SessionView(latest.content, merged[:-1])

    def clear(self) -> None:
        """Forget all temporary turns immediately."""
        self._sessions.clear()


def extract_visible_turns(layout: ScreenOcrLayout) -> tuple[ConversationTurn, ...]:
    """Convert only verified message bubbles into ordered chat turns.

    OCR returns text *and* geometry.  The geometry decides whose bubble a line
    belongs to; textual markers then remove timestamps, headers, Reply quotes,
    and Last Z event cards before they can be mistaken for conversation.
    """
    lines = sorted(layout.lines, key=lambda item: (item.top, item.left))
    animated_emote_lines = _animated_emote_line_indexes(lines, layout.width)
    turns: list[ConversationTurn] = []
    previous_line: OcrLayoutLine | None = None
    ignored_card_line: OcrLayoutLine | None = None
    for line_index, line in enumerate(lines):
        text = " ".join(line.text.split())

        # A Reply quote is context embedded inside a bubble, not a new turn.
        # The following line(s) may be in a different alphabet, so positional
        # grouping is safer than an English-only text filter.
        if (
            line_index in animated_emote_lines
            or _REPLY_MARKER.match(text)
            or _SYSTEM_CARD.fullmatch(text)
        ):
            ignored_card_line = line
            previous_line = None
            continue
        if ignored_card_line is not None and _continues_card(line, ignored_card_line, layout.width):
            ignored_card_line = line
            previous_line = None
            continue
        ignored_card_line = None

        role = _role_for_line(line, layout.width)
        if role is None or _is_non_message(text):
            previous_line = None
            continue
        can_extend = (
            turns
            and previous_line is not None
            and turns[-1].role == role
            and line.top >= previous_line.top
            and line.top - previous_line.bottom
            <= max(12, int((previous_line.bottom - previous_line.top) * 0.6))
            and abs(line.left - previous_line.left) <= max(36, int(layout.width * 0.12))
        )
        if can_extend:
            turns[-1] = ConversationTurn(role, f"{turns[-1].content} {text}")
        else:
            turns.append(ConversationTurn(role, text))
        previous_line = line
    return tuple(turns)


def has_trailing_animated_emote(layout: ScreenOcrLayout) -> bool:
    """Whether the newest meaningful visible item is a stock animated emote."""
    lines = sorted(layout.lines, key=lambda item: (item.top, item.left))
    emote_lines = _animated_emote_line_indexes(lines, layout.width)
    if not emote_lines:
        return False
    meaningful = [
        index
        for index, line in enumerate(lines)
        if not _is_non_message(" ".join(line.text.split()))
    ]
    return bool(meaningful) and max(emote_lines) == max(meaningful)


def is_last_z_animated_emote_text(value: str) -> bool:
    """Recognize the distinctive stock wording of a Last Z animated emote.

    This is the no-layout fallback.  It intentionally identifies only the
    small known set of phrases used by the illustrated fire-character cards;
    regular player chat about fires remains ordinary chat.
    """
    normalized = " ".join(str(value or "").split())
    return bool(_ANIMATED_EMOTE.search(normalized))


def _animated_emote_line_indexes(
    lines: list[OcrLayoutLine],
    width: int,
) -> set[int]:
    """Find one compact, left-side OCR group containing a stock emote phrase."""
    ignored: set[int] = set()
    max_gap = max(28, int(width * 0.035))
    max_left_shift = max(52, int(width * 0.14))
    for start, first in enumerate(lines):
        if _role_for_line(first, width) != "user":
            continue
        group_indexes = [start]
        parts = [" ".join(first.text.split())]
        previous = first
        for index in range(start + 1, min(len(lines), start + 5)):
            candidate = lines[index]
            if _role_for_line(candidate, width) != "user":
                break
            if candidate.top - previous.bottom > max_gap:
                break
            if abs(candidate.left - previous.left) > max_left_shift:
                break
            group_indexes.append(index)
            parts.append(" ".join(candidate.text.split()))
            previous = candidate
        if _ANIMATED_EMOTE.search(" ".join(parts)):
            ignored.update(group_indexes)
    return ignored


def _role_for_line(line: OcrLayoutLine, width: int) -> TurnRole | None:
    # The OCR image is twice the source resolution, but the ratio is the same.
    # Leave only a narrow middle band unknown rather than guessing a speaker.
    # In the supplied Last Z screens player text starts around 0.18--0.25 and
    # PEva's green bubble text around 0.30 or farther right.
    ratio = line.left / width
    if ratio <= 0.28:
        return "user"
    if ratio >= 0.30:
        return "assistant"
    return None


def _is_non_message(text: str) -> bool:
    if not text or _TIME_OR_DATE.fullmatch(text) or _CHAT_UI.match(text):
        return True
    return is_last_z_sender_header(text) or parse_private_header(text) is not None


def _continues_card(
    line: OcrLayoutLine,
    previous: OcrLayoutLine,
    width: int,
) -> bool:
    """Return whether a nearby OCR line remains inside an ignored card/quote."""
    text = " ".join(line.text.split())
    # A timestamp or a real speaker header always starts a fresh region.
    if (
        _TIME_OR_DATE.fullmatch(text)
        or is_last_z_sender_header(text)
        or parse_private_header(text) is not None
    ):
        return False
    gap = line.top - previous.bottom
    close_vertically = 0 <= gap <= max(24, int((previous.bottom - previous.top) * 1.2))
    near_same_left_edge = abs(line.left - previous.left) <= max(48, int(width * 0.14))
    return close_vertically and near_same_left_edge


def _merge_turns(
    previous: tuple[ConversationTurn, ...],
    current: tuple[ConversationTurn, ...],
    *,
    limit: int,
) -> tuple[ConversationTurn, ...]:
    """Append only a clear suffix/prefix overlap; otherwise reset safely."""
    maximum = min(len(previous), len(current))
    for size in range(maximum, 0, -1):
        if previous[-size:] == current[:size]:
            return (previous + current[size:])[-limit:]
    return current[-limit:]
