#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/mnt/d/projekty/lastZAI"
exec timeout --foreground --signal=TERM --kill-after=10s 90s \
  "${PROJECT_DIR}/.venv-agency/bin/python" -B "${PROJECT_DIR}/tools/wsl_cuda_probe.py"
