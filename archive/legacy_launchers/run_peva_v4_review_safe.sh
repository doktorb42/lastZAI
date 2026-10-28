#!/usr/bin/env bash
# Three review-only V4 proposals with an OS-enforced cutoff.
set -euo pipefail

PROJECT_DIR="/mnt/d/projekty/lastZAI"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MAX_CASES="${PEVA_REVIEW_MAX_CASES:-3}"
MAX_NEW_TOKENS="${PEVA_REVIEW_MAX_NEW_TOKENS:-32}"
TIME_LIMIT_SECONDS="${PEVA_REVIEW_TIME_LIMIT_SECONDS:-600}"
ADAPTER="${PROJECT_DIR}/data/training/runs/peva-qwen2.5-7b-agency-pilot-v4/adapter"
OUTPUT="${PROJECT_DIR}/data/training/agency/real_situation_tests/peva_v4_real_situation_smoke_${STAMP}.json"

exec timeout --foreground --signal=TERM --kill-after=30s "${TIME_LIMIT_SECONDS}s" \
  "${PROJECT_DIR}/.venv-agency/bin/python" -B "${PROJECT_DIR}/agency_real_situation_test.py" \
  --adapter "${ADAPTER}" --max-cases "${MAX_CASES}" --max-new-tokens "${MAX_NEW_TOKENS}" \
  --time-limit-seconds "${TIME_LIMIT_SECONDS}" --output "${OUTPUT}"
