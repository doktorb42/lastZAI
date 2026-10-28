"""Mostek zgodności do zachowanego planu starej nawigacji PM.

Nowy runtime PEva go nie importuje.  Plik istnieje wyłącznie dlatego, że
`archive/legacy_runtime/main_legacy.py` zachowuje dawną ścieżkę importu.
"""

from archive.legacy_runtime.private_chat_navigation import (
    PrivateChatTransition,
    next_private_chat_transition,
    next_private_reply_transition,
)

__all__ = [
    "PrivateChatTransition",
    "next_private_chat_transition",
    "next_private_reply_transition",
]
