"""Mostek zgodności do ręcznego Review Studio Agency w Treningu."""

# Zachowanie modułu ``os`` pod dawną nazwą utrzymuje test atomowego zapisu.
import os

from trening.agency_review_studio import (
    DEFAULT_STORE, ReviewError, ReviewStudio, StudioHandler, main, serve,
)

__all__ = ["DEFAULT_STORE", "ReviewError", "ReviewStudio", "StudioHandler", "serve", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
