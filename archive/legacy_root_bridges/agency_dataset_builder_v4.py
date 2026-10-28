"""Mostek zgodności do budownika historycznego Agency V4 w Treningu."""

from trening.agency_dataset_builder_v4 import (
    EXPECTED_TOTAL, FROZEN_BENCHMARK, MANIFEST_PATH, OUTPUT_DIR,
    PRIVATE_BENCHMARK, SEED, TRAIN_PATH, V3_TOTAL, VALIDATION_PATH,
    audit_rows, build, main, prepared_rows, source_rows, split_rows,
)

__all__ = [
    "OUTPUT_DIR", "TRAIN_PATH", "VALIDATION_PATH", "MANIFEST_PATH", "SEED",
    "FROZEN_BENCHMARK", "PRIVATE_BENCHMARK", "V3_TOTAL", "EXPECTED_TOTAL",
    "source_rows", "prepared_rows", "split_rows", "audit_rows", "build", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
