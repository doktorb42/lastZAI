"""Przeźroczysty mostek do zachowanego silnika patchowania Last Z.

Normalny start PEva nie importuje ani nie uruchamia patchowania.  Ta nazwa
pozostaje wyłącznie dla świadomego, ręcznego narzędzia zgodności.
"""

from __future__ import annotations

import sys
from types import ModuleType

from archive.legacy_runtime import update_patch as _implementation


class _CompatibilityModule(ModuleType):
    """Utrzymaj jedną implementację także przy podmianach w starych testach."""

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
    _implementation.main()
