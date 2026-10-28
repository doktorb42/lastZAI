"""Mostek zgodności do ręcznego runnera treningu Agency V4."""

from trening.train_agency_pilot_v4 import FROZEN_BENCHMARK_SHA256, _frozen_benchmark_status, main, preflight

__all__ = ["FROZEN_BENCHMARK_SHA256", "_frozen_benchmark_status", "preflight", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
