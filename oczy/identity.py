"""Konserwatywne rozpoznanie nagłówka otwartego prywatnego czatu.

Nie zgadujemy autora po treści bąbelka.  Parser przyjmuje wyłącznie osobny
tekst nagłówka otrzymany z lokalnego Androidowego OCR, a jego wynik wystarcza
wyłącznie do podglądu odpowiedzi.  UID nie może być wyprowadzony z nazwy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationHeader:
    display_name: str
    alliance: str | None
    confidence: float
    source: str = "private_header_ocr"


# A Last Z sender line begins with account VIP level (V8), then HQ level
# (LV.27), then a gender icon and only then the visible nickname.  Neither
# level nor icon is part of a player name.
_VIP_PREFIX = re.compile(r"^\s*v\.?\s*\d+\s*", re.IGNORECASE)
_LEVEL_PREFIX = re.compile(r"^\s*lv\.?\s*\d+\s*", re.IGNORECASE)
_GENDER_MARKER = re.compile(r"[♀♂]\s*")
# On the small outlined Last Z font ML Kit occasionally reads the male icon
# as a standalone ``O``.  It is removed only after both the VIP and HQ-level
# prefixes were seen; that preserves ordinary names such as "O Connor".
_GENDER_OCR_TOKEN_AFTER_FULL_PREFIX = re.compile(r"^\s*(?:o|0|@)\s+", re.IGNORECASE)
_ALLIANCE = re.compile(r"[\[(]([A-Za-z0-9_-]{2,12})[\])]", re.IGNORECASE)
_UNSAFE_HEADER = re.compile(r"\b(?:reply|translate|report|mention|private\s+chat|group\s+chat)\b", re.IGNORECASE)
# OCR can lose the leading ``V`` and glue the gender icon to the HQ level,
# producing e.g. ``8 LV.27O DOKTORB``. This is still unmistakably a sender
# line from a bubble, never the centred title of a private conversation.
_GAME_SENDER_PREFIX = re.compile(
    r"^\s*(?:(?:v\.?\s*)?\d{1,2}\s+)?lv\.?\s*\d{1,3}"
    r"(?:\s*[♀♂o0@])?(?=\s|\[|\(|$)\s*",
    re.IGNORECASE,
)
# This narrower form is what a corrupted bubble header looks like when ML Kit
# loses the VIP's leading ``V`` or attaches the gender glyph to ``LV.27``.
# It is intentionally narrower than ``_GAME_SENDER_PREFIX`` because older
# compatible packet fixtures still use a correctly formatted ``LV.27`` value
# as their supplied header.
_CORRUPTED_BUBBLE_HEADER = re.compile(
    r"^\s*(?:\d{1,2}\s+lv\.?\s*\d{1,3}(?:[o0@])?|"
    r"(?:(?:v\.?\s*\d+\s+)?lv\.?\s*\d{1,3})[o0@])(?=\s|\[|\(|$)",
    re.IGNORECASE,
)
# These are Last Z modal-window titles, not private-conversation recipients.
# Keeping this list deliberately short is important: a real player may choose
# an unusual nickname, but an open profile must never be mistaken for a chat
# header just because the chat remains visible behind it.
_BLOCKING_OVERLAY_TITLES = frozenset({"survivor profile", "their profile"})
_GENERIC_TITLES = _BLOCKING_OVERLAY_TITLES | frozenset({"profile", "chat", "notice", "last z"})


def is_blocking_overlay_title(value: str) -> bool:
    """Return true only for a known Last Z window covering a PM.

    This consumes no text from the chat itself.  It lets the OCR pipeline
    quarantine a frame whose dedicated header crop belongs to a modal profile
    window rather than to the conversation behind it.
    """
    normalized = " ".join(str(value or "").split()).casefold()
    return normalized in _BLOCKING_OVERLAY_TITLES


def is_last_z_sender_header(value: str) -> bool:
    """Whether text is a game-style sender line above a message bubble.

    This accepts small OCR corruptions of the VIP/gender decoration solely so
    the line can be quarantined from private-chat history. It does not grant
    the line authority to identify a private conversation or a player UID.
    """
    raw = " ".join(str(value or "").split())
    match = _GAME_SENDER_PREFIX.match(raw)
    if match is None:
        return False
    remainder = raw[match.end():]
    return bool(re.search(r"[A-Za-z0-9]", remainder))


def is_corrupted_last_z_sender_header(value: str) -> bool:
    """Whether OCR produced the unsafe glued/missing-V bubble-header form."""
    raw = " ".join(str(value or "").split())
    return bool(_CORRUPTED_BUBBLE_HEADER.match(raw))


def parse_private_header(
    value: str,
    *,
    trusted_aliases: Iterable[str] = (),
    dedicated_header: bool = False,
) -> ConversationHeader | None:
    """Return a temporary chat name only from a verified header source.

    ``dedicated_header`` is true only for the centred title crop supplied by
    the Android Helper.  A plain title there identifies this *open session*,
    but never creates a durable player identity or UID.  Text recovered from
    a message bubble must keep the stricter game-marker/confirmed-alias rule.
    """
    raw = " ".join(str(value or "").split())
    if not raw or raw.casefold() in _GENERIC_TITLES or _UNSAFE_HEADER.search(raw):
        return None
    # A dedicated title is allowed to be plain (``DOKTORB``), but a
    # game-style line proves the Android crop reached a bubble instead. Fail
    # closed instead of cleaning that bubble into a plausible recipient.
    if dedicated_header and is_corrupted_last_z_sender_header(raw):
        return None
    aliases = tuple(
        normalized
        for alias in trusted_aliases
        if (normalized := " ".join(str(alias or "").split()))
    )
    for normalized in aliases:
        if normalized and raw.casefold() == normalized.casefold():
            return ConversationHeader(normalized, None, 0.99, "private_alias_ocr")
    alliance_match = _ALLIANCE.search(raw)
    without_vip = _VIP_PREFIX.sub("", raw)
    without_level = _LEVEL_PREFIX.sub("", without_vip)
    without_markers = _GENDER_MARKER.sub("", without_level)
    saw_vip = without_vip != raw
    saw_level = without_level != without_vip
    if saw_vip and saw_level:
        without_markers = _GENDER_OCR_TOKEN_AFTER_FULL_PREFIX.sub("", without_markers)
    name = _ALLIANCE.sub("", without_markers)
    # OCR occasionally retains decorative icons as punctuation.  They do not
    # carry identity and can be stripped without turning arbitrary body text
    # into a plausible name.
    name = re.sub(r"^[^\w]+|[^\w .'-]+$", "", name).strip()
    if not name or len(name) > 42 or not re.search(r"[A-Za-z0-9]", name):
        return None
    for normalized in aliases:
        if name.casefold() == normalized.casefold():
            return ConversationHeader(normalized, None, 0.99, "private_alias_ocr")
    # An alliance tag or a level prefix proves this came from the game-style
    # header rather than a random message line.  A plain title is accepted
    # only when the Android Helper extracted it from its separate, centred
    # conversation-header region; this can never turn a bubble into identity.
    if alliance_match is None and without_markers == raw:
        plain_title = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._'\-]{1,31}", name)
        if not dedicated_header or plain_title is None:
            return None
        return ConversationHeader(name, None, 0.86, "private_dedicated_header_ocr")
    alliance = alliance_match.group(1).upper() if alliance_match is not None else None
    confidence = 0.97 if alliance is not None else 0.86
    return ConversationHeader(name, alliance, confidence)


def parse_game_sender_header(value: str) -> ConversationHeader | None:
    """Parse one Last Z bubble sender line for a future Group Chat reader.

    It accepts only a game-style prefix such as ``V8 LV.27``, an alliance
    tag, or a real gender marker. A plain OCR line is rejected, so this helper
    cannot turn arbitrary Group Chat body text into a speaker. It does not
    create a UID, persistent identity, or reply permission. Private messages
    continue to use the dedicated title at the top of their conversation.
    """
    raw = " ".join(str(value or "").split())
    match = _GAME_SENDER_PREFIX.match(raw)
    if match is None:
        return None
    remainder = raw[match.end():].strip()
    alliance_match = _ALLIANCE.search(remainder)
    name = _ALLIANCE.sub("", remainder)
    name = re.sub(r"^[^\w]+|[^\w .'-]+$", "", name).strip()
    if not name or len(name) > 42 or not re.search(r"[A-Za-z0-9]", name):
        return None
    return ConversationHeader(
        name,
        alliance_match.group(1).upper() if alliance_match is not None else None,
        0.97 if alliance_match is not None else 0.90,
        "game_sender_header_ocr",
    )
