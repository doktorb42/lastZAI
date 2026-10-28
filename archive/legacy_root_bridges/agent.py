"""Mostek zgodności do agenta zachowanego starego runtime.

Nowy ``peva_runtime.py`` nie korzysta z tej ścieżki.  Stary agent pozostaje
dostępny dla historycznego ``main_legacy.py`` i testów, lecz jego właściwy kod
jest odseparowany w ``archive/legacy_runtime``.
"""

from archive.legacy_runtime.agent import (
    DEFAULT_DB_PATH,
    DEFAULT_OWN_UID,
    AgentConfig,
    AgentResult,
    LastZAgent,
    PendingReply,
    main,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_OWN_UID",
    "AgentConfig",
    "AgentResult",
    "LastZAgent",
    "PendingReply",
    "main",
]


if __name__ == "__main__":
    main()
