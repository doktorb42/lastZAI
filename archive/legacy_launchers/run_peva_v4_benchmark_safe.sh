#!/usr/bin/env bash
# Frozen V4 benchmark with an OS-enforced cutoff.
set -euo pipefail

PROJECT_DIR="/mnt/d/projekty/lastZAI"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TIME_LIMIT_SECONDS="${PEVA_BENCHMARK_TIME_LIMIT_SECONDS:-1800}"
ADAPTER="${PROJECT_DIR}/data/training/runs/peva-qwen2.5-7b-agency-pilot-v4/adapter"
OUTPUT="${PROJECT_DIR}/data/training/agency/benchmarks/peva_agency_v0_pilot_v4_${STAMP}.json"

exec timeout --foreground --signal=TERM --kill-after=30s "${TIME_LIMIT_SECONDS}s" \
  "${PROJECT_DIR}/.venv-agency/bin/python" -B "${PROJECT_DIR}/agency_benchmark.py" \
  --adapter "${ADAPTER}" --output "${OUTPUT}"
