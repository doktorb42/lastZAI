"""Przeźroczysty mostek do zachowanej, starej pętli PlayerLoop.

Nowy runtime PEva nie importuje tej pętli. Właściwy kod pozostaje wyłącznie w
``archive/legacy_runtime`` dla zgodności i audytu zachowanego transportu.
"""

from __future__ import annotations

import sys
from types import ModuleType

from archive.legacy_runtime import player_loop as _implementation


__all__ = [
    "PlayerLoopConfig",
    "PrivateReplySafety",
    "PlayerLoopRecorder",
    "PlayerLoop",
]


class _CompatibilityModule(ModuleType):
    """Przekaż stare importy i ich podmiany do jednej implementacji."""

    def __getattr__(self, name: str):
        return getattr(_implementation, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("__") or name in {"_implementation", "__all__"}:
            super().__setattr__(name, value)
            return
        setattr(_implementation, name, value)

    def __delattr__(self, name: str) -> None:
        if name.startswith("__") or name in {"_implementation", "__all__"}:
            super().__delattr__(name)
            return
        delattr(_implementation, name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(dir(_implementation)))


sys.modules[__name__].__class__ = _CompatibilityModule
