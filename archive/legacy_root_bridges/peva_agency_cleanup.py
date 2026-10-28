"""Mostek zgodności do konserwatywnego czyszczenia danych Agency w Treningu."""

from __future__ import annotations

import sys
from types import ModuleType

from trening import agency_cleanup as _impl


__all__ = [
    "DATABASES", "BACKUP_DIR", "DB_NAMES", "OG_UID", "FALSE_MEMORY",
    "CLEANUP_MANIFEST_PATH", "utc_now", "sha256", "backup_info", "decision",
    "inspect_and_apply", "write_manifest", "main",
]
_FORWARDED_SETTINGS = frozenset({"DATABASES", "BACKUP_DIR", "CLEANUP_MANIFEST_PATH"})


class _CompatibilityModule(ModuleType):
    """Przekazuje dawne, testowe podmiany ścieżek do właściwego modułu."""

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        if name in __all__:
            return getattr(_impl, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: object) -> None:
        if name in _FORWARDED_SETTINGS:
            setattr(_impl, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _CompatibilityModule


if __name__ == "__main__":
    raise SystemExit(_impl.main())
