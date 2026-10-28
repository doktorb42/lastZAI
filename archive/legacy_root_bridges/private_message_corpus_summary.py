"""Mostek zgodności do anonimowego podsumowania danych treningowych."""

from trening.private_message_corpus_summary import (
    CONVERSATION_DB,
    OUTPUT,
    REVIEW_CANDIDATES,
    build_summary,
    main,
    summarize_message_records,
    write_summary,
)

__all__ = [
    "CONVERSATION_DB",
    "REVIEW_CANDIDATES",
    "OUTPUT",
    "summarize_message_records",
    "build_summary",
    "write_summary",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
