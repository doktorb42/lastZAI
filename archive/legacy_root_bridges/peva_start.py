"""Przeźroczysty mostek do historycznego launchera patcha i Capture.

Zwykłe uruchomienie PEva nie importuje tego modułu.  Mostek zachowuje dawne
punkty podmiany dla testów, podczas gdy działający launcher jest odseparowany
w ``archive/legacy_runtime``.
"""

from __future__ import annotations

import sys
from types import ModuleType

from archive.legacy_runtime import peva_start as _implementation


class _CompatibilityModule(ModuleType):
    """Przekazuj odczyt i podmianę dawnych nazw do jednej implementacji."""

    def __getattr__(self, name: str):
        return getattr(_implementation, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("__") or name == "_implementation":
            super().__setattr__(name, value)
            return
        setattr(_implementation, name, value)

    def __delattr__(self, name: str) -> None:
        if name.startswith("__") or name == "_implementation":
            super().__delattr__(name)
            return
        delattr(_implementation, name)


sys.modules[__name__].__class__ = _CompatibilityModule


if __name__ == "__main__":
    raise SystemExit(_implementation.main())
