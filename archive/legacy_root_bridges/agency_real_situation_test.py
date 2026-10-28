"""Mostek zgodności do ręcznego testu realnych sytuacji Agency w Treningu."""

from trening.agency_real_situation_test import (
    REAL_CANDIDATES, RESULTS_DIR, atomic_json_write, build_case, generate_case,
    interpret_generation, load_real_candidates, main, output_path,
    progress_report, summarize,
)

__all__ = [
    "REAL_CANDIDATES", "RESULTS_DIR", "load_real_candidates", "build_case",
    "interpret_generation", "summarize", "output_path", "atomic_json_write",
    "generate_case", "progress_report", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
