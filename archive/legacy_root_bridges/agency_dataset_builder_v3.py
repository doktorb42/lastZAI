"""Mostek zgodności do budownika historycznego Agency V3 w Treningu."""

from trening.agency_dataset_builder_v3 import (
    EXPECTED_CORRECTIVE_COUNTS, FROZEN_BENCHMARK, MANIFEST_PATH, OUTPUT_DIR,
    PRIVATE_BENCHMARK, SEED, TRAIN_PATH, VALIDATION_PATH, audit_rows, build,
    main, prepared_rows, source_rows, split_rows,
)

__all__ = [
    "OUTPUT_DIR", "TRAIN_PATH", "VALIDATION_PATH", "MANIFEST_PATH", "SEED",
    "FROZEN_BENCHMARK", "PRIVATE_BENCHMARK", "EXPECTED_CORRECTIVE_COUNTS",
    "source_rows", "prepared_rows", "split_rows", "audit_rows", "build", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
