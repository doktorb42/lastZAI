"""Mostek zgodności do eksportu kandydatów Agency w Treningu."""

from trening.agency_real_candidate_export import (
    CONVERSATION_DB, FEEDBACK_DB, OUTPUT, export_real_candidates, main,
    write_export,
)

__all__ = ["CONVERSATION_DB", "FEEDBACK_DB", "OUTPUT", "export_real_candidates", "write_export", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
