#!/usr/bin/env bash
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_ROOT}/outputs/logs"
DEBUG_DIR="${PROJECT_ROOT}/outputs/debug"
LOG_FILE="${LOG_DIR}/env_check.log"

mkdir -p "${LOG_DIR}" "${DEBUG_DIR}"
cd "${PROJECT_ROOT}" || exit 1

if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  {
    echo "Step 0 environment check"
    echo "========================"
    echo "[FAIL] python3: not found in PATH"
  } | tee "${LOG_FILE}"
  exit 1
fi

"${PYTHON_BIN}" 00_setup/verify_env.py 2>&1 | tee "${LOG_FILE}"
exit "${PIPESTATUS[0]}"
