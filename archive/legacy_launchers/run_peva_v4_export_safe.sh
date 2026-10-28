#!/usr/bin/env bash
# Explicit test-only V4 HF merge and Q6_K GGUF conversion with a hard cutoff.
set -euo pipefail

PROJECT_DIR="/mnt/d/projekty/lastZAI"
TIME_LIMIT_SECONDS="${PEVA_EXPORT_TIME_LIMIT_SECONDS:-3600}"

exec timeout --foreground --signal=TERM --kill-after=30s "${TIME_LIMIT_SECONDS}s" \
  "${PROJECT_DIR}/.venv-agency/bin/python" -B "${PROJECT_DIR}/export_peva_agency_v4_gguf.py" \
  "$@"
