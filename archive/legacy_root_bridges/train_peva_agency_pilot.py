"""Mostek zgodności do wspólnej bazy historycznych pilotów Agency."""

from __future__ import annotations

from typing import Any

from trening import train_agency_pilot_base as _implementation


def __getattr__(name: str) -> Any:
    """Expose the live shared base, including its tested private helpers."""

    return getattr(_implementation, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_implementation)))


def main(argv: list[str] | None = None) -> int:
    return _implementation.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
