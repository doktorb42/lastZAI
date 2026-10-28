#!/usr/bin/env bash
# Run the one-epoch PEva V3 pilot with an OS-enforced time and GPU-use boundary.
set -euo pipefail

PROJECT_DIR="/mnt/d/projekty/lastZAI"
# One V3 epoch is expected to finish well inside this bound.  Timeout also
# prevents an interrupted process from consuming power overnight.
TIME_LIMIT_SECONDS="${PEVA_TRAIN_TIME_LIMIT_SECONDS:-2700}"

exec timeout --foreground --signal=TERM --kill-after=30s "${TIME_LIMIT_SECONDS}s" \
  "${PROJECT_DIR}/.venv-agency/bin/python" -B "${PROJECT_DIR}/train_peva_agency_pilot_v3.py" \
  "$@"
