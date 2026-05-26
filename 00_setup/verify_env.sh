#!/usr/bin/env bash
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_ROOT}/outputs/logs"
LOG_FILE="${LOG_DIR}/env_check.log"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_ROOT}" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  {
    echo "Step 0 environment check"
    echo "========================"
    echo "[FAIL] python3: not found in PATH"
  } | tee "${LOG_FILE}"
  exit 1
fi

python3 00_setup/verify_env.py 2>&1 | tee "${LOG_FILE}"
exit "${PIPESTATUS[0]}"
