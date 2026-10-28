"""Mostek zgodności do audytu danych Agency w Treningu."""

from trening.agency_dataset_audit import (
    BENCHMARK,
    COMBINED_MANIFEST,
    DEFAULT_DATASET,
    EXPECTED_COUNTS,
    REAL_CANDIDATES,
    SYNTHETIC_DIR,
    V0_EXPECTED_COUNTS,
    V1_DATASET,
    V1_EXPECTED_COUNTS,
    audit_combined_pilot,
    audit_dataset,
    main,
    write_combined_manifest,
)

__all__ = [
    "SYNTHETIC_DIR", "DEFAULT_DATASET", "V1_DATASET", "REAL_CANDIDATES",
    "COMBINED_MANIFEST", "BENCHMARK", "V0_EXPECTED_COUNTS",
    "V1_EXPECTED_COUNTS", "EXPECTED_COUNTS", "audit_dataset",
    "audit_combined_pilot", "write_combined_manifest", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
