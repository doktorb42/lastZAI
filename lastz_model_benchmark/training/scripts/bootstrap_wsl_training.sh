#!/usr/bin/env bash
# Create the isolated WSL Action V1 training environment.
# Nothing is installed unless --execute is passed.  The virtual environment,
# resolver log, and freeze are all kept beneath lastz_model_benchmark/training.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAINING_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Keep the validated CUDA 12.6 environment distinct from a preserved failed
# CUDA 13 attempt.  This also makes the runtime/toolchain visible in reports.
VENV_DIR="${TRAINING_ROOT}/.venv-wsl-cu126"
REPORT_DIR="${TRAINING_ROOT}/outputs/reports"
REQUIREMENTS="${TRAINING_ROOT}/requirements-wsl.txt"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
EXECUTE=0

if [[ "${1:-}" == "--execute" ]]; then
  EXECUTE=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--execute]" >&2
  exit 2
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "[BLOCKED] ${PYTHON_BIN} is unavailable in WSL." >&2
  exit 2
fi
if [[ ! -f "${REQUIREMENTS}" ]]; then
  echo "[BLOCKED] missing ${REQUIREMENTS}" >&2
  exit 2
fi
if ! nvidia-smi >/dev/null 2>&1; then
  echo "[BLOCKED] WSL NVIDIA GPU is unavailable; refusing CPU-oriented setup." >&2
  exit 2
fi

mkdir -p "${REPORT_DIR}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
LOG="${REPORT_DIR}/wsl_training_bootstrap_${STAMP}.log"
FREEZE="${REPORT_DIR}/wsl_training_freeze_${STAMP}.txt"

{
  echo "training_root=${TRAINING_ROOT}"
  echo "venv=${VENV_DIR}"
  echo "python=$(${PYTHON_BIN} --version)"
  echo "gpu=$(nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free,compute_cap --format=csv,noheader,nounits)"
  echo "requirements=${REQUIREMENTS}"
  echo "execute=${EXECUTE}"
} | tee "${LOG}"

if [[ "${EXECUTE}" != "1" ]]; then
  echo "[OK] dry run. Re-run with --execute to create ${VENV_DIR} and install the pinned stack." | tee -a "${LOG}"
  exit 0
fi

if [[ -e "${VENV_DIR}" ]]; then
  echo "[BLOCKED] ${VENV_DIR} already exists; preserve it and inspect its freeze rather than overwriting it." | tee -a "${LOG}" >&2
  exit 2
fi

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip | tee -a "${LOG}"
"${VENV_DIR}/bin/python" -m pip install -r "${REQUIREMENTS}" | tee -a "${LOG}"
"${VENV_DIR}/bin/python" -m pip check | tee -a "${LOG}"
"${VENV_DIR}/bin/python" -m pip freeze | tee "${FREEZE}" >> "${LOG}"

echo "[OK] WSL training environment created. Freeze: ${FREEZE}" | tee -a "${LOG}"
