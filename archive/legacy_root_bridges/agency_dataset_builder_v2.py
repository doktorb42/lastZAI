"""Mostek zgodności do budownika historycznego Agency V2 w Treningu."""

from trening.agency_dataset_builder_v2 import (
    FROZEN_BENCHMARK, MANIFEST_PATH, OUTPUT_DIR, PRIVATE_BENCHMARK, SEED,
    TRAIN_PATH, VALIDATION_PATH, audit_rows, build, main, prepared_rows,
    source_rows, split_rows,
)

__all__ = [
    "OUTPUT_DIR", "TRAIN_PATH", "VALIDATION_PATH", "MANIFEST_PATH", "SEED",
    "FROZEN_BENCHMARK", "PRIVATE_BENCHMARK", "source_rows", "prepared_rows",
    "split_rows", "audit_rows", "build", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
