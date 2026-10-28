#!/usr/bin/env bash
# Check the V4 data, CUDA and QLoRA environment without loading the 7B model.
set -euo pipefail

PROJECT_DIR="/mnt/d/projekty/lastZAI"
TIME_LIMIT_SECONDS="${PEVA_PREFLIGHT_TIME_LIMIT_SECONDS:-180}"

exec timeout --foreground --signal=TERM --kill-after=10s "${TIME_LIMIT_SECONDS}s" \
  "${PROJECT_DIR}/.venv-agency/bin/python" -B "${PROJECT_DIR}/train_peva_agency_pilot_v4.py" \
  --dry-run
