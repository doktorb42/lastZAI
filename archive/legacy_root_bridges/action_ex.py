"""Przeźroczysty mostek do wykonawcy zachowanego starego runtime.

Nowy runtime PEva nie importuje wykonawcy.  Właściwy kod pozostał dostępny
wyłącznie dla historycznego łańcucha w ``archive/legacy_runtime``.
"""

from __future__ import annotations

import sys
from types import ModuleType

from archive.legacy_runtime import action_ex as _implementation


__all__ = [
    "DEFAULT_MAX_MESSAGE_CHARS",
    "ActionPolicy",
    "ActionResult",
    "ActionExecutor",
    "build_private_reply_action",
    "execute_action_result",
    "execute_action",
    "main",
]


class _CompatibilityModule(ModuleType):
    """Przekaż stare importy i testowe podmiany do jednej implementacji."""

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


if __name__ == "__main__":
    _implementation.main()
