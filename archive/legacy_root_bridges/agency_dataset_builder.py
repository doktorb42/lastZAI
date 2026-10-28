"""Mostek zgodności do budownika danych Agency w Treningu."""

from trening.agency_dataset_builder import (
    MANIFEST_PATH,
    OUTPUT_DIR,
    SEED,
    TRAIN_PATH,
    VALIDATION_PATH,
    _canonical_json,
    _messages,
    build,
    main,
    prepared_rows,
    source_rows,
    split_rows,
)

__all__ = [
    "OUTPUT_DIR", "TRAIN_PATH", "VALIDATION_PATH", "MANIFEST_PATH", "SEED",
    "source_rows", "prepared_rows", "split_rows", "build", "main",
    "_canonical_json", "_messages",
]


if __name__ == "__main__":
    raise SystemExit(main())
