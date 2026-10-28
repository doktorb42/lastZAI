"""Mostek zgodności do zamrożonego benchmarku Agency w Treningu."""

from trening.agency_benchmark import (
    BASE_ID,
    BASE_REVISION,
    BENCHMARK,
    RESULTS,
    _generate,
    _load_runner,
    _messages,
    evaluate,
    is_benchmark_action_safe,
    main,
    preflight,
    score_generated_cases,
)

__all__ = [
    "BASE_ID", "BASE_REVISION", "BENCHMARK", "RESULTS", "preflight",
    "_messages", "_load_runner", "_generate", "is_benchmark_action_safe",
    "score_generated_cases", "evaluate", "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
