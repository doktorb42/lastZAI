"""Mostek zgodności do ręcznego przeglądu danych treningowych."""

from trening.conversation_review import (
    interactive_agency_review,
    interactive_review,
    main,
    render_agency_review,
    render_player_review,
)

__all__ = [
    "render_player_review",
    "interactive_review",
    "render_agency_review",
    "interactive_agency_review",
    "main",
]


if __name__ == "__main__":
    main()
