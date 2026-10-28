#!/usr/bin/env bash
# One clean-base V4 QLoRA run with an OS-enforced power-use boundary.
set -euo pipefail

PROJECT_DIR="/mnt/d/projekty/lastZAI"
TIME_LIMIT_SECONDS="${PEVA_TRAIN_TIME_LIMIT_SECONDS:-2700}"

exec timeout --foreground --signal=TERM --kill-after=30s "${TIME_LIMIT_SECONDS}s" \
  "${PROJECT_DIR}/.venv-agency/bin/python" -B "${PROJECT_DIR}/train_peva_agency_pilot_v4.py" \
  "$@"
