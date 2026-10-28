"""Mostek zgodności do deterministycznego evaluatora benchmarku w Treningu."""

from trening.benchmark_evaluator import (
    DEFAULT_BENCHMARK, evaluate, load_cases, main, run_private_benchmark,
)

__all__ = ["DEFAULT_BENCHMARK", "evaluate", "load_cases", "run_private_benchmark", "main"]


if __name__ == "__main__":
    main()
