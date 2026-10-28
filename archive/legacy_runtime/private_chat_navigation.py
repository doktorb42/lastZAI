"""Shared, fail-closed route planning for the private-chat UI.

This module deliberately plans only known screen transitions.  Target UID
verification stays in ``adb_controll`` where the Unity click evidence is
available.  Keeping the route here lets both the legacy application and the
event-driven PlayerLoop follow the same map/chat/private-chat sequence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrivateChatTransition:
    """One safe UI transition, or a terminal ready/wait result."""

    action: str | None
    step: str
    expected_screen: str | None = None


def next_private_chat_transition(screen: object) -> PrivateChatTransition:
    """Return the next known step needed to show the private-chat list."""
    normalized = str(screen or "UNKNOWN").strip().upper()
    if normalized in {"WORLD_MAP", "HQ_BASE"}:
        return PrivateChatTransition("open_chat", "open_chat", "ALLIANCE_CHAT")
    if normalized == "ALLIANCE_CHAT":
        return PrivateChatTransition(
            "open_private_chat", "open_private_chat", "PRIVATE_CHAT"
        )
    if normalized == "PRIVATE_CONVERSATION":
        return PrivateChatTransition("back", "back_to_private_chat", "PRIVATE_CHAT")
    if normalized == "PRIVATE_CHAT":
        return PrivateChatTransition(None, "private_chat_ready")
    return PrivateChatTransition(None, "wait_for_known_screen")


def next_private_reply_transition(
    screen: object,
    *,
    target_verified: bool,
) -> PrivateChatTransition:
    """Plan the next step before a physical private-reply send.

    ``prepare_private_reply_target`` performs UID/room verified selection but
    never enters text or taps Send.  A later direct-send decision is legal only
    when the target conversation is verified.
    """
    normalized = str(screen or "UNKNOWN").strip().upper()
    if normalized == "PRIVATE_CONVERSATION" and target_verified:
        return PrivateChatTransition(None, "send_ready")
    if normalized in {"PRIVATE_CHAT", "PRIVATE_CONVERSATION"}:
        return PrivateChatTransition(
            "prepare_private_reply_target",
            "verify_target",
            "PRIVATE_CONVERSATION",
        )
    return next_private_chat_transition(normalized)
