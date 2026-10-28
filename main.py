"""Zwykły start nowej PEva.

Uruchomienie tego pliku otwiera jedno okno, z którego PEva ładuje model oraz
Oczy w SAFE.  Nie ma parametrów ``--`` ani ścieżki automatycznego wysłania.

Poprzedni runtime jest zachowany w ``archive.legacy_runtime.main_legacy``.
Kilka dawnych narzędzi może nadal zażądać jego nazw; są ładowane leniwie,
dlatego normalny start nie importuje starego transportu ADB.
"""

from __future__ import annotations

from typing import Any


def _legacy() -> Any:
    from archive.legacy_runtime import main_legacy

    return main_legacy


def _settings_recorder(*args: Any, **kwargs: Any) -> Any:
    """Wąska zgodność dla starego testu ustawień bez starego startu PEva."""
    legacy = _legacy()
    # ``tests.test_runtime_settings`` historically patches ``main.DATA_DIR``.
    # Keep that narrow patch point working after the old module moved to the
    # archive, without importing it during a normal PEva start.
    override = globals().get("DATA_DIR")
    original = legacy.DATA_DIR
    if override is not None:
        legacy.DATA_DIR = override
    try:
        return legacy._settings_recorder(*args, **kwargs)
    finally:
        legacy.DATA_DIR = original


def __getattr__(name: str) -> Any:
    """Udostępnij stare publiczne nazwy tylko dla zgodności podczas migracji."""
    legacy = _legacy()
    try:
        return getattr(legacy, name)
    except AttributeError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error


def main() -> None:
    from peva_app import main as start_window

    start_window()


if __name__ == "__main__":
    main()
